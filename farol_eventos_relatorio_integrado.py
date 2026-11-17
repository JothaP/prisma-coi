"""Rotinas territoriais e temporais do Farol para o Relatório Integrado (M7).

Cópia das FUNÇÕES PURAS correspondentes ao Módulo 3 (produção), sem
importar/executar a página Streamlit. Não escreve em planilhas ou Supabase.
Atualizar este arquivo se essas regras do M3 forem modificadas.
Os tests/test_ri7_eventos_farol.py verificam equivalência de algoritmos.
"""
from __future__ import annotations

import io
import math
import os
import re
import sqlite3
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pyproj import CRS, Transformer
from shapely import wkb
from shapely.ops import transform as transformar_geometria

FUSO_FAROL = ZoneInfo("America/Fortaleza")
RAIZ_PROJETO = Path(__file__).resolve().parent
DIRETORIO_KMZ = str(RAIZ_PROJETO / "geodados" / "municipios")
ARQUIVO_KMZ_PADRAO = str(RAIZ_PROJETO / "TERESINA.kmz")

def normalizar_texto(valor):
    if valor is None:
        return ""
    texto = str(valor).strip().upper()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def normalizar_cabecalho(valor):
    texto = normalizar_texto(valor)
    texto = texto.replace("º", "O")
    texto = re.sub(r"[^A-Z0-9]+", "", texto)
    return texto


def parse_float(valor):
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    texto = str(valor).strip().replace(" ", "")
    if not texto:
        return None
    try:
        # Trata 5,1234 e 5.1234, além de 1.234,56.
        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        else:
            texto = texto.replace(",", ".")
        return float(texto)
    except Exception:
        return None


def limpar_nome_bairro(valor):
    """Chave única para O.S., eventos e feições, sem o rótulo genérico BAIRRO.

    Preserva o nome original de exibição em `bairro["nome"]` e remove apenas
    o prefixo isolado no começo, nunca a palavra dentro do nome do bairro.
    """
    texto = normalizar_texto(valor)
    texto = re.sub(r"\s*\(\s*PARCIAL\s*\)\s*$", "", texto)
    texto = re.sub(
        r"^(?:BAIRROS?|SUB[\s-]*AREAS?|SUB[\s-]*REGIAO(?:ES)?)"
        r"(?:\s*[-–—:]\s*|\s+)", "", texto
    )
    # Trata Villa Inglesa e Vila Inglesa como a mesma localidade, sem
    # aproximar nomes de bairros diferentes nem alterar o rótulo original.
    texto = re.sub(r"\bVILLA\b", "VILA", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" \t.,;:!?")
    return texto


def normalizar_localidade_busca(valor):
    """Normaliza separadores e grafia para comparar nomes completos no endereço."""
    texto = re.sub(r"[^A-Z0-9]+", " ", normalizar_texto(valor))
    texto = re.sub(r"\bVILLA\b", "VILA", texto)
    return re.sub(r"\s+", " ", texto).strip()


def endereco_contem_localidade(endereco, localidade):
    """Busca nome COMPLETO em limites de palavras, evitando fragmentos e ruas homônimas.

    Não confunde 'Dom Avelar' com 'Dom Avelar II' nem 'Vila Inglesa' com
    'Vila Inglesa Nova' quando a localidade exata é outra: para o primeiro
    caso, a subárea adicional requer identificação explícita. O nome de uma
    rua/avenida, sozinho, não estabelece que a O.S. pertence à localidade.
    """
    area = normalizar_localidade_busca(limpar_nome_bairro(localidade))
    texto = normalizar_localidade_busca(endereco)
    if not area or not texto or len(area) < 5:
        return False
    # Rejeita tokens genéricos que não individualizam uma localidade.
    if area in {"BAIRRO", "REGIAO", "AREA", "SETOR", "TODOS", "PARCIAL"}:
        return False
    padrao = r"(?<![A-Z0-9])" + re.escape(area) + r"(?![A-Z0-9])"
    for achado in re.finditer(padrao, texto):
        anteriores = texto[:achado.start()].split()
        posteriores = texto[achado.end():].split()
        if anteriores and anteriores[-1] in {
            "RUA", "R", "AV", "AVENIDA", "TRAVESSA", "TV",
            "ESTRADA", "RODOVIA", "ALAMEDA", "PRACA", "PCA",
        }:
            continue
        # Não confundir um conjunto com sua extensão explicitamente nomeada.
        if posteriores and posteriores[0] in {
            "I", "II", "III", "IV", "V", "1", "2", "3", "4", "5",
            "NOVO", "NOVA",
        }:
            continue
        return True
    return False


def nomes_reconhecidos_feicao(item):
    """Nomes exatos de um polígono: subárea, bairro e aliases cadastrais.

    Permite reconhecer o campo 'name' de feições adicionadas no PRISMA-GEO,
    além de NM_BAIRRO, sem aproximação textual entre localidades diferentes.
    """
    if item.get("sem_nome"):
        return set()
    valores = [
        item.get("nome_normalizado") or item.get("nome"),
        *(item.get("nomes_alternativos") or []),
    ]
    return {chave for valor in valores if (chave := limpar_nome_bairro(valor))}


def indice_nomes_bairros(bairros_geograficos, municipio):
    """Catálogo de bairros e subáreas do município, por nomes exatos."""
    cidade = normalizar_municipio(municipio)
    nomes = set()
    for item in (bairros_geograficos or []):
        if normalizar_municipio(item.get("municipio", "")) == cidade:
            nomes.update(nomes_reconhecidos_feicao(item))
    return nomes


def resolver_nome_bairro(nome, nomes_disponiveis):
    """Equivalência conservadora: nomes exatos ou prefixo genérico 'Bairro'."""
    chave = limpar_nome_bairro(nome)
    if chave in nomes_disponiveis:
        return chave
    sem_prefixo = re.sub(r"^BAIRRO\s+", "", chave).strip()
    if sem_prefixo and sem_prefixo in nomes_disponiveis:
        return sem_prefixo
    return chave


def evento_abrange_municipio(valor):
    """Identifica abrangência municipal explícita, sem inferir por bairro vazio."""
    texto = normalizar_texto(valor)
    return bool(re.search(
        r"\b(?:TODO\s+O\s+MUNICIPIO|MUNICIPIO\s+INTEIRO|"
        r"TODA\s+A\s+CIDADE|CIDADE\s+INTEIRA|"
        r"TODO\s+O\s+TERRITORIO\s+MUNICIPAL|"
        r"TODOS\s+OS\s+BAIRROS)\b", texto
    ))


def extrair_bairros_evento(valor, municipio="", bairros_geograficos=None):
    """Lê bairros informados em Áreas Impactadas e valida contra a base local.

    Reconhece o rótulo 'Áreas Afetadas' mesmo precedido pelo município.
    Desdobra 'Itararé 1 e 2' em dois bairros, independentemente da cobertura
    geográfica: cada bairro é validado separadamente ao desenhar o mapa.
    Preserva rótulos sem polígono no diagnóstico, sem inventar geometrias.
    """
    if valor is None or str(valor).strip().lower() in ("", "nan", "none"):
        return []
    nomes = indice_nomes_bairros(bairros_geograficos, municipio)
    if evento_abrange_municipio(valor):
        return [{"bairro": nome, "parcial": False, "original": "Todo o município"}
                for nome in sorted(nomes)]
    texto = str(valor).replace("\r", "\n")
    texto = re.sub(r"(?i)\bBAIRROS?\s*(?:IMPACTADOS?|AFETADOS?)?\s*:", "\n", texto)
    texto = re.sub(
        r"(?i)\bSUB[\s-]*[ÁA]REAS?\s*(?:IMPACTADAS?|AFETADAS?)?\s*:", "\n", texto
    )
    texto = re.sub(r"(?i)\b(?:ÁREAS?|AREAS?)\s+(?:IMPACTADAS?|AFETADAS?)\s*:", "\n", texto)
    partes = re.split(r"[,;\n]+", texto)
    saida = []
    vistos = set()
    for parte in partes:
        original = parte.strip(" \t-*•.,;:!?")
        if not original:
            continue
        # Cidade isolada não é automaticamente interpretada como todos os bairros.
        if municipio and normalizar_municipio(original) == normalizar_municipio(municipio):
            continue
        parcial = bool(re.search(r"\(\s*PARCIAL\s*\)", original, flags=re.I))
        chave = resolver_nome_bairro(original, nomes)
        candidatos = [(chave, original)]
        if chave not in nomes:
            # A enumeração numérica explícita é inequívoca, mesmo se um ou
            # ambos os polígonos estiverem ausentes do cadastro cartográfico.
            # Isso permite marcar o bairro disponível e diagnosticar o ausente.
            abreviado = re.fullmatch(r"(.+?)\s+(\d+)\s+E\s+(\d+)", chave)
            if abreviado:
                radical, numero_1, numero_2 = abreviado.groups()
                candidatos = [(f"{radical} {numero_1}", f"{radical} {numero_1}"),
                              (f"{radical} {numero_2}", f"{radical} {numero_2}")]
            else:
                # Separar 'Bairro São Domingos e Guabiraba' é uma intenção
                # explícita do campo; nos demais nomes só separar se TODOS os
                # componentes existirem no catálogo do mesmo município.
                pedacos = [resolver_nome_bairro(p, nomes) for p in re.split(r"\s+E\s+", chave)]
                lista_explicita = bool(re.match(r"^BAIRRO\b", normalizar_texto(original)))
                if len(pedacos) >= 2 and (lista_explicita or
                                          (nomes and all(p in nomes for p in pedacos))):
                    candidatos = [(p, p) for p in pedacos]
        for bairro, origem in candidatos:
            if bairro and bairro not in vistos:
                vistos.add(bairro)
                saida.append({"bairro": bairro, "parcial": parcial, "original": origem})
    return saida


def normalizar_municipio(valor):
    """Usa a mesma chave para Cidade da planilha e nome do arquivo KMZ."""
    return re.sub(r"[^A-Z0-9]", "", normalizar_texto(valor))


def carregar_kmz_bytes(kmz_bytes):
    with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as z:
        nomes_kml = [n for n in z.namelist() if n.lower().endswith(".kml")]
        if not nomes_kml:
            raise ValueError("O KMZ não contém arquivo KML.")
        data = z.read(nomes_kml[0])
    return carregar_kml_bytes(data)


def carregar_kml_bytes(kml_bytes):
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    raiz = ET.fromstring(kml_bytes)
    bairros = []

    for placemark in raiz.findall(".//k:Placemark", ns):
        nome_el = placemark.find("k:name", ns)
        nome = nome_el.text.strip() if nome_el is not None and nome_el.text else ""
        if not nome:
            continue

        poligonos = []
        for poly in placemark.findall(".//k:Polygon", ns):
            outer = poly.find("k:outerBoundaryIs/k:LinearRing/k:coordinates", ns)
            if outer is None or not outer.text:
                continue
            coords = []
            for item in outer.text.strip().split():
                partes = item.split(",")
                if len(partes) >= 2:
                    try:
                        lon = float(partes[0])
                        lat = float(partes[1])
                        coords.append((lat, lon))
                    except ValueError:
                        pass
            if len(coords) >= 3:
                poligonos.append(coords)

        if poligonos:
            bairros.append({
                "nome": nome,
                "nome_normalizado": limpar_nome_bairro(nome),
                "poligonos": poligonos,
            })

    return bairros


def carregar_gpkg(caminho):
    """Lê TODAS as geometrias poligonais do GPKG, sem exigir nome de bairro.

    As coordenadas são a fonte de verdade. Os atributos são opcionais e
    servem para nomear a feição e relacioná-la a eventos quando disponíveis.
    Preserva anéis internos (furos) e converte o SRC original para WGS84.
    """
    registros = []
    with sqlite3.connect(f"file:{caminho}?mode=ro", uri=True) as conexao:
        camadas = conexao.execute(
            "SELECT table_name, srs_id FROM gpkg_contents WHERE data_type='features'"
        ).fetchall()
        for tabela, srs_id in camadas:
            info = conexao.execute(
                "SELECT column_name, srs_id FROM gpkg_geometry_columns WHERE table_name=?",
                (tabela,),
            ).fetchone()
            if not info:
                continue
            campo_geometria, srs_geom = info
            quoted = lambda x: '"' + x.replace('"', '""') + '"'
            nomes = [
                r[1] for r in conexao.execute(f"PRAGMA table_info({quoted(tabela)})")
            ]
            campos = {normalizar_cabecalho(c): c for c in nomes}
            campo_bairro = next(
                (campos[k] for k in ("NMBAIRRO", "NOMEBAIRRO", "BAIRRO", "NOME")
                 if k in campos), None,
            )
            # No GPKG, NM_BAIRRO pode identificar o bairro principal,
            # enquanto name/NM_SUBAREA descrevem uma subárea desenhada
            # posteriormente. Priorizar só NM_BAIRRO torna a subárea invisível.
            campo_subarea = next(
                (campos[k] for k in (
                    "NMSUBAREA", "NOMESUBAREA", "SUBAREA",
                    "NOMESUBREGIAO", "SUBREGIAO",
                    "NAME", "NOMELOCALIDADE", "LOCALIDADE",
                ) if k in campos and campos[k] != campo_bairro), None,
            )
            campo_nome_aux = next(
                (campos[k] for k in ("NAME", "LOCALIDADE", "NOMELOCALIDADE")
                 if k in campos and campos[k] not in (campo_bairro, campo_subarea)), None,
            )
            campo_mun = next(
                (campos[k] for k in ("NMMUN", "MUNICIPIO", "NOMEMUNICIPIO", "CIDADE", "NMRGI")
                 if k in campos), None,
            )
            src_id = srs_geom or srs_id
            linha_srs = conexao.execute(
                "SELECT organization, organization_coordsys_id, definition "
                "FROM gpkg_spatial_ref_sys WHERE srs_id=?", (src_id,),
            ).fetchone()
            if not linha_srs:
                continue
            org, cod, definicao = linha_srs
            try:
                origem = CRS.from_user_input(f"{org}:{cod}") if org and cod else CRS.from_wkt(definicao)
            except Exception:
                origem = CRS.from_wkt(definicao)
            conversor = None if origem.equals(CRS.from_epsg(4326)) else Transformer.from_crs(
                origem, 4326, always_xy=True
            )
            # Não descartar a camada quando não há campo de nome:
            # a geometria pode ser desenhada normalmente.
            colunas = [campo_geometria]
            for campo in (campo_bairro, campo_subarea, campo_nome_aux, campo_mun):
                if campo and campo not in colunas:
                    colunas.append(campo)
            consulta = f"SELECT {', '.join(quoted(c) for c in colunas)} FROM {quoted(tabela)}"
            for numero_feicao, row in enumerate(conexao.execute(consulta), start=1):
                atributos = dict(zip(colunas, row))
                bruto = atributos.get(campo_geometria)
                if not bruto:
                    continue
                try:
                    bruto = bytes(bruto)
                    if len(bruto) < 8 or bruto[:2] != b"GP":
                        continue
                    envelope = (bruto[3] >> 1) & 7
                    tamanhos = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
                    if envelope not in tamanhos:
                        continue
                    geometria = wkb.loads(bruto[8 + tamanhos[envelope]:])
                    if conversor:
                        geometria = transformar_geometria(conversor.transform, geometria)
                    if geometria.is_empty:
                        continue
                    if geometria.geom_type == "Polygon":
                        partes = [geometria]
                    elif geometria.geom_type == "MultiPolygon":
                        partes = list(geometria.geoms)
                    else:
                        continue
                    poligonos, aneis_por_poligono = [], []
                    for parte in partes:
                        exterior = [(float(y), float(x)) for x, y in parte.exterior.coords]
                        if len(exterior) < 4 or not all(
                            math.isfinite(lat) and math.isfinite(lon)
                            and -90 <= lat <= 90 and -180 <= lon <= 180
                            for lat, lon in exterior
                        ):
                            continue
                        aneis = [exterior]
                        for interno in parte.interiors:
                            furo = [(float(y), float(x)) for x, y in interno.coords]
                            if len(furo) >= 4:
                                aneis.append(furo)
                        poligonos.append(exterior)
                        aneis_por_poligono.append(aneis)
                except (ValueError, TypeError, OverflowError, AttributeError):
                    # Uma feição malformada não impede a leitura das demais.
                    continue
                if not poligonos:
                    continue

                def nome_valido(valor):
                    texto = str(valor or "").strip()
                    return "" if texto.upper() in {
                        "", "NAN", "NONE", "NULL", "NOVO ELEMENTO", "NEW FEATURE"
                    } else texto

                nome_bairro = nome_valido(atributos.get(campo_bairro)) if campo_bairro else ""
                nome_subarea = nome_valido(atributos.get(campo_subarea)) if campo_subarea else ""
                nome_auxiliar = nome_valido(atributos.get(campo_nome_aux)) if campo_nome_aux else ""
                # O nome específico prevalece sobre o bairro de origem; ambos
                # continuam pesquisáveis, inclusive em eventos já cadastrados.
                nome_real = nome_subarea or nome_bairro or nome_auxiliar
                nomes_alternativos = list(dict.fromkeys(
                    nome for nome in (nome_bairro, nome_subarea, nome_auxiliar)
                    if nome and limpar_nome_bairro(nome)
                ))
                subarea_explicita = bool(
                    nome_subarea
                    and (
                        campo_subarea and normalizar_cabecalho(campo_subarea) in {
                            "NMSUBAREA", "NOMESUBAREA", "SUBAREA",
                            "NOMESUBREGIAO", "SUBREGIAO"
                        }
                        or nome_bairro and
                        limpar_nome_bairro(nome_subarea) != limpar_nome_bairro(nome_bairro)
                    )
                )
                rotulo = nome_real or f"Área sem nome ({tabela} #{numero_feicao})"
                municipio_atributo = atributos.get(campo_mun) if campo_mun else ""
                municipio = municipio_atributo or nome_municipio_arquivo(caminho)
                registros.append({
                    "nome": rotulo,
                    "nome_normalizado": limpar_nome_bairro(nome_real) if nome_real else "",
                    "nomes_alternativos": nomes_alternativos,
                    "tipo_area": "Subárea" if subarea_explicita else "Bairro",
                    "sem_nome": not bool(nome_real),
                    "municipio": normalizar_municipio(municipio),
                    "poligonos": poligonos,
                    "aneis_por_poligono": aneis_por_poligono,
                })
    return registros


def nome_municipio_arquivo(caminho):
    nome = os.path.splitext(os.path.basename(caminho))[0]
    nome = re.sub(r"^(?:NM_MUN|NM_RGI)[_\s-]*", "", nome, flags=re.I)
    # Também permite "SUBAREAS_Teresina.gpkg" e nomes equivalentes.
    # Se o arquivo possuir NM_MUN, o atributo específico prevalece.
    if re.search(r"(?i)(?:^|[_\s-])TERESINA(?:$|[_\s-])", nome):
        return "TERESINA"
    if re.search(r"(?i)(?:^|[_\s-])TIMON(?:$|[_\s-])", nome):
        return "TIMON"
    return nome


def preparar_poligonos_para_mapa(registros, tolerancia=0.00007):
    """Simplifica as geometrias apenas para desenhar, preservando todos os anéis.

    Os polígonos e furos ORIGINAIS permanecem disponíveis para os cruzamentos
    espaciais. A geometria não depende da presença de atributos cadastrais.
    """
    from shapely.geometry import Polygon

    for bairro in registros:
        visuais, aneis_visuais = [], []
        origem = bairro.get("aneis_por_poligono") or [
            [coords] for coords in bairro.get("poligonos", [])
        ]
        for aneis in origem:
            if not aneis or len(aneis[0]) < 4:
                continue
            atual = aneis
            if len(aneis[0]) >= 25:
                try:
                    geom = Polygon(
                        [(lon, lat) for lat, lon in aneis[0]],
                        holes=[
                            [(lon, lat) for lat, lon in furo]
                            for furo in aneis[1:]
                        ],
                    )
                    simplificado = geom.simplify(tolerancia, preserve_topology=True)
                    if not simplificado.is_empty and simplificado.geom_type == "Polygon":
                        novos = [
                            [(float(lat), float(lon)) for lon, lat in simplificado.exterior.coords]
                        ]
                        novos.extend(
                            [(float(lat), float(lon)) for lon, lat in ring.coords]
                            for ring in simplificado.interiors
                        )
                        if len(novos[0]) >= 4:
                            atual = novos
                except (ValueError, TypeError):
                    pass
            visuais.append(atual[0])
            aneis_visuais.append(atual)
        bairro["poligonos_mapa"] = visuais
        bairro["aneis_mapa"] = aneis_visuais
    return registros


def carregar_geometrias_municipais(arquivos_assinados):
    """Carrega GeoPackage, KMZ e KML por município, sem impedir uso do mapa em caso de erro."""
    todos = []
    for caminho, _tamanho, _mtime in arquivos_assinados:
        extensao = os.path.splitext(caminho)[1].lower()
        municipio = normalizar_municipio(nome_municipio_arquivo(caminho))
        try:
            if extensao == ".gpkg":
                registros = carregar_gpkg(caminho)
            elif extensao in (".kmz", ".kml"):
                with open(caminho, "rb") as arquivo:
                    dados = arquivo.read()
                registros = carregar_kmz_bytes(dados) if extensao == ".kmz" else carregar_kml_bytes(dados)
                registros = [{**bairro, "municipio": municipio} for bairro in registros]
            else:
                continue
            todos.extend(preparar_poligonos_para_mapa(registros))
        except Exception:
            # Não exibe avisos de arquivos sem cobertura, mantendo os eventos na planilha.
            continue
    return todos


def descobrir_kmz():
    """Compatibilidade com nome antigo; descobre KMZ, KML e GPKG da pasta."""
    arquivos = []
    if os.path.isdir(DIRETORIO_KMZ):
        for nome in sorted(os.listdir(DIRETORIO_KMZ)):
            if nome.lower().endswith((".gpkg", ".kmz", ".kml")):
                caminho = os.path.join(DIRETORIO_KMZ, nome)
                if os.path.isfile(caminho):
                    arquivos.append(caminho)
    # Carrega TODOS os GPKG, inclusive complementos de subáreas, e não
    # apenas o primeiro GPKG de cada município. Evita duplicar o KMZ/KML
    # antigo quando o município já dispõe de GPKG.
    geopackages = [a for a in arquivos if a.lower().endswith(".gpkg")]
    municipios_gpkg = {
        normalizar_municipio(nome_municipio_arquivo(a)) for a in geopackages
    }
    escolhidos = list(geopackages)
    legados = {}
    for caminho in arquivos:
        if caminho.lower().endswith(".gpkg"):
            continue
        municipio = normalizar_municipio(nome_municipio_arquivo(caminho))
        if not municipio or municipio in municipios_gpkg:
            continue
        if municipio not in legados or caminho.lower().endswith(".kmz"):
            legados[municipio] = caminho
    escolhidos.extend(legados.values())
    if (os.path.isfile(ARQUIVO_KMZ_PADRAO)
            and "TERESINA" not in municipios_gpkg
            and "TERESINA" not in legados):
        escolhidos.append(ARQUIVO_KMZ_PADRAO)
    assinaturas = []
    for caminho in escolhidos:
        try:
            info = os.stat(caminho)
            assinaturas.append((caminho, info.st_size, info.st_mtime_ns))
        except OSError:
            continue
    # Recalcula a chave de cada feição inclusive se o GeoPackage/KMZ tiver
    # sido recuperado de um cache criado antes da normalização do prefixo.
    # `nome` permanece intacto para rótulos e pop-ups do mapa.
    geometrias = carregar_geometrias_municipais(tuple(assinaturas))
    for bairro in geometrias:
        bairro["nome_normalizado"] = limpar_nome_bairro(
            bairro.get("nome") or bairro.get("nome_normalizado", "")
        )
    return geometrias


def ponto_em_poligono(lat, lon, poligono):
    # Ray casting. poligono: [(lat, lon), ...]
    dentro = False
    x = lon
    y = lat
    n = len(poligono)
    j = n - 1
    for i in range(n):
        yi, xi = poligono[i]
        yj, xj = poligono[j]
        cruza = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi)
        if cruza:
            dentro = not dentro
        j = i
    return dentro


def ponto_em_bairro(lat, lon, bairro):
    aneis_por_poligono = bairro.get("aneis_por_poligono") or [
        [p] for p in bairro["poligonos"]
    ]
    for aneis in aneis_por_poligono:
        if (ponto_em_poligono(lat, lon, aneis[0])
                and not any(ponto_em_poligono(lat, lon, furo) for furo in aneis[1:])):
            return True
    return False


def indexar_geometrias_evento(bairros_geograficos, cidade_evento, areas_evento):
    """Relaciona bairros e subáreas pelo nome exato e município."""
    nomes = {area["bairro"] for area in areas_evento}
    indice = {nome: [] for nome in nomes}
    for bairro in (bairros_geograficos or []):
        if bairro.get("sem_nome") or normalizar_municipio(bairro.get("municipio", "")) != cidade_evento:
            continue
        for nome in nomes_reconhecidos_feicao(bairro) & nomes:
            indice[nome].append(bairro)
    return indice


def identificar_correspondencia_territorial_evento_os(
        os_reg, cidade_evento, areas_evento, bairros_geograficos=None,
        municipal=False, indice_geometrias=None):
    """Retorna (área, critério), sem aproximar localidades distintas.

    Se a área afetada tem polígono e a O.S. possui coordenadas, a geometria
    é decisiva: estar FORA impede vínculo pelo texto do bairro/endereço.
    Sem polígono cadastrado, usa bairro exato ou, em última instância,
    localidade completa no Endereço. Nunca cruza municípios diferentes.
    """
    if normalizar_municipio(os_reg.get("Cidade", "")) != cidade_evento:
        return None, None
    if municipal:
        return "Todo o município", "Município"
    if not areas_evento:
        return None, None

    indice = (
        indice_geometrias if indice_geometrias is not None
        else indexar_geometrias_evento(bairros_geograficos, cidade_evento, areas_evento)
    )
    lat = normalizar_coordenada(os_reg.get("Latitude", ""), "lat")
    lon = normalizar_coordenada(os_reg.get("Longitude", ""), "lon")
    tem_coordenadas = lat is not None and lon is not None

    # Prioridade global: todas as áreas deste evento com polígonos,
    # inclusive subáreas sobrepostas a bairros maiores.
    if tem_coordenadas:
        for area in areas_evento:
            nome = area["bairro"]
            if any(ponto_em_bairro(lat, lon, poligono) for poligono in indice.get(nome, [])):
                return nome, "Polígono"

    bairro_textual = limpar_nome_bairro(os_reg.get("Bairro", ""))
    if bairro_textual:
        for area in areas_evento:
            nome = area["bairro"]
            # Não usar texto para contrariar o polígono quando a posição
            # geográfica permite verificar que a O.S. está fora da área.
            if bairro_textual == nome and (not indice.get(nome) or not tem_coordenadas):
                return nome, "Bairro cadastrado"

    endereco = os_reg.get("Endereço", "")
    for area in areas_evento:
        nome = area["bairro"]
        if not indice.get(nome) and endereco_contem_localidade(endereco, nome):
            return nome, "Endereço"
    return None, None


def normalizar_coordenada(valor, tipo="lat"):
    n = parse_float(valor)
    if n is None or not math.isfinite(n) or n == 0:
        return None
    if tipo == "lat" and not (-90 <= n <= 90):
        return None
    if tipo == "lon" and not (-180 <= n <= 180):
        return None
    return round(float(n), 6)


def classificar_status_evento(valor):
    status = normalizar_texto(valor)
    if any(p in status for p in ("FINALIZ", "ENCERR", "CONCLUI", "CONCLUID", "NORMALIZ", "RESOLVID", "CANCELAD")):
        return "Finalizado", "#2eaa59"
    if "PENDENTE" in status:
        return "Pendente", "#FDE68A"  # Amarelo claro para eventos pendentes.
    if any(p in status for p in ("PROGRAM", "AGEND", "PREVIST", "PLANEJ")):
        return "Programado", "#f2b233"
    if any(p in status for p in ("ANDAMENTO", "EXECU", "INICIAD", "ATIVO", "ABERTO", "OCORRENCIA", "EM CURSO")):
        return "Em andamento", "#e04747"
    return "Status não classificado", "#8b95a7"


def interpretar_data_operacional(valor):
    """Converte horários de eventos, inclusive '24/10/2026 09:30h'.

    Uma data sem hora não é tratada como meia-noite: sem horário não se pode
    confirmar vínculo temporal com O.S. Nenhuma data é deslocada entre fusos.
    """
    if valor is None or str(valor).strip().lower() in ("", "nan", "none", "nat", "-", "--"):
        return pd.NaT
    if isinstance(valor, (datetime, pd.Timestamp)):
        t = pd.Timestamp(valor)
        return t.tz_localize(None) if t.tzinfo is not None else t
    texto = str(valor).strip()
    # Exportação de eventos: hh:mmh ou hh:mm:ssh.
    texto = re.sub(r"(?i)(?<=\d)\s*h\.?\s*$", "", texto).strip()
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{1,2}-\d{1,2}", texto):
        return pd.NaT
    try:
        resultado = pd.to_datetime(texto, dayfirst=True, errors="coerce")
        if pd.isna(resultado):
            return pd.NaT
        t = pd.Timestamp(resultado)
        return t.tz_localize(None) if t.tzinfo is not None else t
    except (ValueError, TypeError, OverflowError):
        return pd.NaT


def classificar_os_por_evento(os_grupo, eventos_relacionados):
    """Associa cada O.S. a TODOS os eventos elegíveis separadamente.

    Intervalo por evento: [início - 1h, término real + 3h].
    Na falta de término, apenas eventos ainda abertos vão até o momento atual.
    Correspondência territorial calculada individualmente com geometrias
    no momento de montar os dados da concentração.
    """
    vinculadas, nao_vinculadas = [], []
    agora = pd.Timestamp.now(tz=FUSO_FAROL).tz_localize(None)
    for indice_os, os_reg in os_grupo.iterrows():
        data_os = interpretar_data_operacional(os_reg.get("Dt. Emissão", ""))
        cidade_os = normalizar_municipio(os_reg.get("Cidade", ""))
        bairro_os = limpar_nome_bairro(os_reg.get("Bairro", ""))
        protocolos = []
        criterios_vinculo = []
        if pd.notna(data_os) and eventos_relacionados is not None and not eventos_relacionados.empty:
            for _, evento in eventos_relacionados.iterrows():
                if cidade_os != normalizar_municipio(evento.get("Cidade", "")):
                    continue
                # Quando há resultado territorial georreferenciado, ele é
                # específico da O.S.: não generalizar para toda a concentração.
                if "_indices_os_correspondentes" in evento.index:
                    ids_territoriais = evento.get("_indices_os_correspondentes")
                    if indice_os not in (ids_territoriais or ()):
                        continue
                else:
                    # Compatibilidade com chamadas sem os metadados de geometria.
                    bairros = [limpar_nome_bairro(b) for b in
                               str(evento.get("Bairros coincidentes", "")).split(",")]
                    if ("Todo o município" not in str(evento.get("Bairros coincidentes", ""))
                            and bairro_os not in bairros):
                        continue
                inicio = interpretar_data_operacional(evento.get("Início", ""))
                fim = interpretar_data_operacional(evento.get("Término Real", ""))
                encerrado = classificar_status_evento(evento.get("Status", ""))[0] == "Finalizado"
                if pd.isna(inicio):
                    continue
                if pd.isna(fim):
                    if encerrado:
                        continue
                    # Evento ainda aberto: não estender a janela para o futuro.
                    limite_superior = agora
                else:
                    if fim < inicio:
                        continue
                    limite_superior = fim + pd.Timedelta(hours=3)
                if inicio - pd.Timedelta(hours=1) <= data_os <= limite_superior:
                    protocolo = str(evento.get("Protocolo", "") or "").strip()
                    if protocolo.lower() in {"", "nan", "none", "-", "--"}:
                        # Mantém eventos sem protocolo distinguíveis por ativo.
                        protocolo = str(evento.get("Código do Ativo", "") or evento.get("Serviço", "") or "Evento").strip()
                    protocolos.append(protocolo)
                    if "_criterios_os_correspondentes" in evento.index:
                        criterio = evento.get("_criterios_os_correspondentes")
                        if isinstance(criterio, dict) and criterio.get(indice_os):
                            criterios_vinculo.append(criterio[indice_os])
        linha = os_reg.copy()
        linha["Evento(s) vinculado(s)"] = ", ".join(dict.fromkeys(protocolos))
        linha["Critério do vínculo"] = ", ".join(dict.fromkeys(criterios_vinculo))
        if protocolos:
            vinculadas.append(linha)
        else:
            nao_vinculadas.append(linha)
    cols = list(os_grupo.columns) + ["Evento(s) vinculado(s)", "Critério do vínculo"]
    def dataframe(linhas):
        return pd.DataFrame(linhas).reindex(columns=cols) if linhas else pd.DataFrame(columns=cols)
    return dataframe(vinculadas), dataframe(nao_vinculadas)


def evento_pertinente_periodo(evento, inicio, fim):
    """Mesma tolerância do Farol (1h antes/3h depois) em intervalo de datas."""
    comeco = interpretar_data_operacional(evento.get("Início", ""))
    termino = interpretar_data_operacional(evento.get("Término Real", ""))
    finalizado = classificar_status_evento(evento.get("Status", ""))[0] == "Finalizado"
    if finalizado and pd.isna(termino):
        return False
    limite_inicio = pd.Timestamp(inicio)
    limite_fim_exclusivo = pd.Timestamp(fim) + pd.Timedelta(days=1)
    if pd.notna(comeco) and comeco - pd.Timedelta(hours=1) >= limite_fim_exclusivo:
        return False
    if pd.notna(termino) and termino + pd.Timedelta(hours=3) < limite_inicio:
        return False
    return True


@lru_cache(maxsize=3)
def _bairros_por_assinatura(assinatura):
    """Cache de geometrias com invalidação por arquivos modificados."""
    return descobrir_kmz()


def bairros_do_farol():
    """Usa as mesmas camadas do M3, atualizando quando arquivos mudam."""
    arquivos = []
    diretorio = Path(DIRETORIO_KMZ)
    if diretorio.is_dir():
        arquivos.extend(
            p for p in diretorio.iterdir()
            if p.is_file() and p.suffix.lower() in {".gpkg", ".kmz", ".kml"}
        )
    if Path(ARQUIVO_KMZ_PADRAO).is_file():
        arquivos.append(Path(ARQUIVO_KMZ_PADRAO))
    assinatura = tuple(sorted(
        (str(p), p.stat().st_size, p.stat().st_mtime_ns)
        for p in arquivos
    ))
    return _bairros_por_assinatura(assinatura)


def eventos_do_grupo_farol(grupo, eventos, bairros, inicio, fim):
    """Mesmos critérios do dados_relatorio_concentracao do Farol, por O.S.

    A única adaptação é a seleção pelo intervalo de datas do relatório M7.
    A análise geográfica conserva o índice de cada O.S. em metadados temporários.
    """
    if grupo is None or grupo.empty or eventos is None or eventos.empty:
        return pd.DataFrame()
    if "Cidade" not in grupo or "Cidade" not in eventos:
        return pd.DataFrame()
    cidades = {
        normalizar_municipio(c) for c in grupo["Cidade"]
    } - {""}
    eventos_candidatos = eventos.loc[
        eventos["Cidade"].map(normalizar_municipio).isin(cidades)
    ]
    relacionados = []
    for _, evento in eventos_candidatos.iterrows():
        if not evento_pertinente_periodo(evento, inicio, fim):
            continue
        cidade = normalizar_municipio(evento.get("Cidade", ""))
        if not cidade:
            continue
        area_texto = evento.get("Áreas Impactadas", "")
        municipal = evento_abrange_municipio(area_texto)
        areas_evento = extrair_bairros_evento(
            area_texto, evento.get("Cidade", ""), bairros
        )
        chaves_areas = {area["bairro"] for area in areas_evento}
        geometrias = [
            b for b in (bairros or [])
            if normalizar_municipio(b.get("municipio", "")) == cidade
            and not nomes_reconhecidos_feicao(b).isdisjoint(chaves_areas)
        ] if not municipal else []
        indice_geometrias = indexar_geometrias_evento(
            geometrias, cidade, areas_evento
        )
        indices_compativeis = []
        bairros_compativeis = set()
        criterios_por_os = {}
        for idx_os, os_reg in grupo.iterrows():
            nome, criterio = identificar_correspondencia_territorial_evento_os(
                os_reg, cidade, areas_evento, municipal=municipal,
                indice_geometrias=indice_geometrias,
            )
            if nome is not None:
                indices_compativeis.append(idx_os)
                bairros_compativeis.add(nome)
                criterios_por_os[idx_os] = criterio
        if indices_compativeis:
            item = evento.to_dict()
            item["Bairros coincidentes"] = ", ".join(sorted(bairros_compativeis))
            item["_indices_os_correspondentes"] = tuple(indices_compativeis)
            item["_criterios_os_correspondentes"] = criterios_por_os
            relacionados.append(item)
    return pd.DataFrame(relacionados)

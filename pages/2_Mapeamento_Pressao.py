
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import json
import pandas as pd
import folium
from streamlit_folium import st_folium
import os
import simplekml
from datetime import datetime, date, timedelta
from streamlit_autorefresh import st_autorefresh
import io
import uuid
from typing import Optional
import time
import re
import math
import unicodedata

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Mapeamento de Pressão - COI",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded"
)

from auth import exigir_acesso_modulo as _prisma_exigir_acesso
_prisma_exigir_acesso("2")

# Estilização CSS para ocultar navegação padrão
st.markdown(
    """
    <style>
        [data-testid="stSidebarNav"] {
            display: none !important;
        }
        [data-testid="stSidebar"] div.block-container {
            padding-top: 1.5rem;
            padding-bottom: 1rem;
        }
    </style>
    """,
    unsafe_allow_html=True
)

# ============================================================
# TRAVA DE SEGURANÇA E CONTROLE DE SESSÃO
# ============================================================
from auth import verificar_autenticacao, pode_executar_acao, exigir_acao

if not verificar_autenticacao():
    st.warning("Sessão não iniciada ou expirada.")
    if st.button("Ir para o Login"):
        st.switch_page("app.py")
    st.stop()

# ============================================================
# CONSTANTES E ESTRUTURA DE COLUNAS
# ============================================================
LAT_BASE = -5.0892
LON_BASE = -42.8019
SPREADSHEET_ID = "1idCgabn5g5X48hmdE-gqWfrUKUf2XxWGB5M_yJFk0yM"
COLUNAS_PADRAO = ["Data", "Município", "Pontos", "Latitude", "Longitude", "MCA", "Observacao"]
CABECALHO_SHEETS = ["Data", "Município", "Pontos", "Latitude", "Longitude", "MCA", "Observação"]


# ============================================================
# CONFERÊNCIA DO CABEÇALHO E LEITURA DE LAYOUTS LEGADOS
# ============================================================
def _chave_cabecalho(campo):
    texto = unicodedata.normalize("NFKD", str(campo or "").strip().upper())
    return "".join(ch for ch in texto if ch.isalnum() and not unicodedata.combining(ch))


def _data_valida_planilha(valor):
    """Data da célula em formatos usuais do Sheets, sem inventar datas."""
    if isinstance(valor, (datetime, date, pd.Timestamp)):
        try:
            return pd.Timestamp(valor).strftime("%d/%m/%Y")
        except (TypeError, ValueError):
            return ""
    texto = str(valor or "").strip().strip("'")
    if not texto or texto.lower() in {"nan", "none", "nat", "-"}:
        return ""
    for formato in (
        "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(texto, formato).strftime("%d/%m/%Y")
        except ValueError:
            pass
    # Algumas células do Sheets contêm a data como número de série.
    if re.fullmatch(r"[0-9]{5}(?:[.,][0-9]+)?", texto):
        try:
            serial = float(texto.replace(",", "."))
            if 20000 <= serial <= 90000:
                return (datetime(1899, 12, 30) + timedelta(days=serial)).strftime("%d/%m/%Y")
        except (ValueError, OverflowError):
            pass
    return ""


def _numero_valido_planilha(valor, minimo, maximo):
    """Aceita decimal com vírgula/ponto e não aceita NaN/Inf."""
    if valor is None or str(valor).strip().lower() in {"", "nan", "none", "nat"}:
        return None
    texto = str(valor).strip().replace(" ", "")
    if "," in texto and "." in texto:
        texto = (texto.replace(".", "").replace(",", ".")
                 if texto.rfind(",") > texto.rfind(".") else texto.replace(",", ""))
    else:
        texto = texto.replace(",", ".")
    try:
        valor_num = float(texto)
        if math.isfinite(valor_num) and minimo <= valor_num <= maximo:
            return valor_num
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _eh_medicao_completa(campos):
    """Valida os 7 campos de uma medição antes de definir o deslocamento."""
    campos = list(campos) + [""] * max(0, 7 - len(campos))
    data, municipio, ponto, lat, lon, mca, _obs = campos[:7]
    return (
        bool(_data_valida_planilha(data))
        and bool(str(municipio).strip())
        and bool(str(ponto).strip())
        and _numero_valido_planilha(lat, -90, 90) not in (None, 0)
        and _numero_valido_planilha(lon, -180, 180) not in (None, 0)
        and _numero_valido_planilha(mca, 0, float("inf")) is not None
    )


def _formato_cabecalho(cabecalho):
    chaves = [_chave_cabecalho(v) for v in cabecalho]
    esperado = [_chave_cabecalho(v) for v in CABECALHO_SHEETS]
    if chaves[:7] == esperado and not any(chaves[7:]):
        return "sem_id"
    if chaves[:8] == ["ID"] + esperado and not any(chaves[8:]):
        return "com_id"
    return "desconhecido"


def _classificar_linha_medicao(linha, modo_cabecalho):
    """Localiza os dados sem alterar a planilha; aceita linhas A:G e B:H."""
    valores = list(linha) + [""] * max(0, 8 - len(linha))
    sem_id = _eh_medicao_completa(valores[:7])
    com_id = _eh_medicao_completa(valores[1:8])
    # Não usar a primeira hipótese se existir um valor extra não explicado em H.
    if sem_id and not str(valores[7]).strip() and not com_id:
        return "sem_id", valores[:7]
    if com_id and modo_cabecalho == "com_id" and not sem_id:
        return "com_id", valores[1:8]
    if com_id and not sem_id:
        return "com_id", valores[1:8]
    return "invalida", None


def conferir_estrutura_planilha(ws):
    """Não migra nem descarta linhas. Identifica os dois layouts já usados no banco.

    Cabeçalho A:H com ID pode conviver com linhas antigas A:G, gravadas antes
    da correção. Exigir que a linha 2 determine o formato de toda a aba
    bloqueava a aplicação indevidamente.
    """
    cabecalho = ws.row_values(1)
    if not any(str(v).strip() for v in cabecalho):
        exigir_acao("2", "editar")
        ws.update(range_name="A1:G1", values=[CABECALHO_SHEETS], value_input_option="RAW")
        return "sem_id"
    modo = _formato_cabecalho(cabecalho)
    if modo == "desconhecido":
        raise ValueError(
            "Cabeçalho não reconhecido na aba mapeamento_pressao. "
            "Nenhum dado foi modificado. Esperado: Data, Município, Pontos, "
            "Latitude, Longitude, MCA, Observação (com ou sem ID inicial)."
        )
    return modo

# ============================================================
# CONEXÃO COM GOOGLE SHEETS
# ============================================================
@st.cache_resource
def conectar_google_sheets():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    config = st.secrets["gcp_json"]
    credentials_dict = json.loads(config) if isinstance(config, str) else dict(config)
    credentials = Credentials.from_service_account_info(credentials_dict, scopes=scopes)
    gc = gspread.authorize(credentials)
    sh = gc.open_by_key(SPREADSHEET_ID)

    try:
        ws = sh.worksheet("mapeamento_pressao")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="mapeamento_pressao", rows=1000, cols=20)

    # Não insere outra linha de cabeçalho: isso desalinhará novamente o banco.
    conferir_estrutura_planilha(ws)
    return ws


try:
    worksheet = conectar_google_sheets()
except Exception as e:
    st.error(f"❌ Erro ao conferir o banco do Módulo 2: {e}")
    st.stop()

# ============================================================
# FUNÇÕES DE CONVERSÃO E TRATAMENTO DE DADOS
# ============================================================
def normalizar_coluna(nome: str) -> str:
    nome = str(nome).strip().lower()

    mapeamento = {
        "data": "Data",
        "municipio": "Município",
        "município": "Município",
        "pontos": "Pontos",
        "ponto": "Pontos",
        "latitude": "Latitude",
        "lat": "Latitude",
        "longitude": "Longitude",
        "lon": "Longitude",
        "long": "Longitude",
        "mca": "MCA",
        "pressao": "MCA",
        "pressão": "MCA",
        "observacao": "Observacao",
        "observação": "Observacao",
        "obs": "Observacao"
    }

    return mapeamento.get(nome, nome.title())


def parse_float(valor, default=None):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return default

    if isinstance(valor, (int, float)):
        return float(valor)

    try:
        texto = str(valor).strip()

        if not texto or texto.lower() in ("nan", "none", "nat", ""):
            return default

        texto = texto.replace(",", ".").replace(" ", "")

        return float(texto)

    except (ValueError, TypeError):
        return default


def normalizar_coordenada(valor, tipo: str = "lat") -> Optional[float]:
    num = parse_float(valor, default=None)

    if num is None:
        return None

    if tipo == "lat" and not (-90.0 <= num <= 90.0):
        return None

    if tipo == "lon" and not (-180.0 <= num <= 180.0):
        return None

    if num == 0.0:
        return None

    return round(float(num), 6)


def normalizar_data(valor) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""

    if isinstance(valor, (datetime, date)):
        return valor.strftime("%d/%m/%Y")

    texto = str(valor).strip()

    if not texto or texto.lower() in ("nan", "none", "nat", ""):
        return ""

    formatos = [
        "%d/%m/%Y",
        "%d/%m/%y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%Y/%m/%d"
    ]

    for fmt in formatos:
        try:
            return datetime.strptime(texto, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue

    return texto


def carregar_dados() -> pd.DataFrame:
    """Lê o banco por posição, inclusive linhas legadas deslocadas com ID."""
    try:
        valores = worksheet.get_all_values()
    except Exception as e:
        st.error(f"Erro ao ler planilha: {e}")
        return pd.DataFrame(columns=COLUNAS_PADRAO + ["_linha_real"])

    if len(valores) < 2:
        return pd.DataFrame(columns=COLUNAS_PADRAO + ["_linha_real"])

    modo = _formato_cabecalho(valores[0])
    if modo == "desconhecido":
        st.error("Cabeçalho do banco não reconhecido; leitura suspensa para proteger os dados.")
        return pd.DataFrame(columns=COLUNAS_PADRAO + ["_linha_real"])

    registros, linhas_nao_interpretadas = [], []
    for idx_linha, linha in enumerate(valores[1:], start=0):
        if not any(str(v).strip() for v in linha):
            continue
        formato, campos = _classificar_linha_medicao(linha, modo)
        if campos is None:
            linhas_nao_interpretadas.append(idx_linha + 2)
            continue
        registro = dict(zip(COLUNAS_PADRAO, campos))
        registro["Data"] = _data_valida_planilha(registro["Data"])
        registro["_linha_real"] = idx_linha  # Linha física do Sheets: +2.
        registro["_layout_linha"] = formato
        registros.append(registro)

    if linhas_nao_interpretadas:
        amostra = ", ".join(map(str, linhas_nao_interpretadas[:12]))
        restante = " ..." if len(linhas_nao_interpretadas) > 12 else ""
        st.warning(
            f"Módulo 2: {len(linhas_nao_interpretadas)} linha(s) da aba "
            f"mapeamento_pressao não seguem um formato de medição válido "
            f"(linhas {amostra}{restante}). Elas NÃO foram excluídas nem "
            "modificadas; continuam disponíveis no Google Sheets para conferência."
        )

    if not registros:
        return pd.DataFrame(columns=COLUNAS_PADRAO + ["_linha_real"])

    df = pd.DataFrame(registros)
    df["Município"] = df["Município"].astype(str).str.strip()
    df["Pontos"] = df["Pontos"].astype(str).str.strip()
    df["Latitude"] = df["Latitude"].map(lambda v: normalizar_coordenada(v, "lat"))
    df["Longitude"] = df["Longitude"].map(lambda v: normalizar_coordenada(v, "lon"))
    df["MCA"] = df["MCA"].map(lambda v: parse_float(v, 0.0))
    df["Observacao"] = df["Observacao"].map(
        lambda v: "" if str(v).strip().lower() in {"nan", "none", "nat"} else str(v).strip()
    )
    return df[COLUNAS_PADRAO + ["_linha_real"]].reset_index(drop=True)


def limpar_cache():
    st.cache_data.clear()


def adicionar_ponto(
    data_str: str,
    municipio: str,
    pontos: str,
    lat: float,
    lon: float,
    mca: float,
    obs: str
):
    # Cadastro manual usa a mesma prevenção de duplicidades do upload em lote.
    if not pode_executar_acao("2", "criar"):
        st.error("Sem permissão para esta operação.")
        return None
    return adicionar_lote_seguro([[
        str(data_str), str(municipio), str(pontos),
        float(lat), float(lon), float(mca), str(obs or "")
    ]])


def adicionar_lote_seguro(linhas_dados: list):
    if not pode_executar_acao("2", "importar"):
        st.error("Sem permissão para esta operação.")
        return 0
    if not linhas_dados:
        return 0

    df_atual = carregar_dados()
    chaves_existentes = set()
    if not df_atual.empty:
        for _, r in df_atual.iterrows():
            chave = (
                str(r["Data"]).strip(), str(r["Município"]).strip().casefold(),
                str(r["Pontos"]).strip().casefold(),
                f"{float(r['Latitude']):.6f}" if pd.notnull(r["Latitude"]) else "",
                f"{float(r['Longitude']):.6f}" if pd.notnull(r["Longitude"]) else "",
            )
            chaves_existentes.add(chave)

    linhas_novas = []
    for linha in linhas_dados:
        d_val, mun_val, p_val, lat_val, lon_val, mca_val, obs_val = linha
        lat_n = normalizar_coordenada(lat_val, "lat")
        lon_n = normalizar_coordenada(lon_val, "lon")
        mca_n = _numero_valido_planilha(mca_val, 0, float("inf"))
        data_n = _data_valida_planilha(d_val)
        if not data_n or not str(mun_val).strip() or not str(p_val).strip() or lat_n is None or lon_n is None or mca_n is None:
            raise ValueError("Lote contém data, ponto, coordenada ou MCA inválido; nenhum registro foi gravado.")
        chave_nova = (data_n, str(mun_val).strip().casefold(), str(p_val).strip().casefold(),
                      f"{lat_n:.6f}", f"{lon_n:.6f}")
        if chave_nova not in chaves_existentes:
            linhas_novas.append([data_n, str(mun_val).strip(), str(p_val).strip(),
                                 f"{lat_n:.6f}", f"{lon_n:.6f}", str(mca_n), str(obs_val or "")])
            chaves_existentes.add(chave_nova)

    if linhas_novas:
        # Respeita o cabeçalho real sem reescrever linhas antigas A:G.
        modo = conferir_estrutura_planilha(worksheet)
        if modo == "com_id":
            dados = [[f"MP-{uuid.uuid4().hex[:16].upper()}"] + campos for campos in linhas_novas]
            tabela = "A1:H"
        else:
            dados = linhas_novas
            tabela = "A1:G"
        worksheet.append_rows(dados, value_input_option="RAW", table_range=tabela)
        time.sleep(0.3)
        limpar_cache()
        return len(linhas_novas)
    return 0


def atualizar_ponto(
    linha_real: int,
    data_str: str,
    municipio: str,
    pontos: str,
    lat: float,
    lon: float,
    mca: float,
    obs: str
) -> bool:
    if not pode_executar_acao("2", "editar"):
        st.error("Sem permissão para esta operação.")
        return False
    try:
        target_row = int(linha_real) + 2
        atual = worksheet.row_values(target_row)
        modo = conferir_estrutura_planilha(worksheet)
        formato, _ = _classificar_linha_medicao(atual, modo)
        if formato == "invalida":
            raise ValueError(
                f"Linha {target_row} não corresponde a uma medição reconhecida. "
                "A edição foi bloqueada para proteger os dados."
            )
        data_n = _data_valida_planilha(data_str)
        lat_n = normalizar_coordenada(lat, "lat")
        lon_n = normalizar_coordenada(lon, "lon")
        mca_n = _numero_valido_planilha(mca, 0, float("inf"))
        if not data_n or lat_n is None or lon_n is None or mca_n is None:
            raise ValueError("Data, coordenadas ou MCA inválido.")
        campos = [data_n, str(municipio), str(pontos), f"{lat_n:.6f}",
                  f"{lon_n:.6f}", str(mca_n), str(obs or "")]
        # Preserva a coluna ID de registros B:H e o formato A:G nos legados.
        colunas = ("B", "H") if formato == "com_id" else ("A", "G")
        worksheet.update(range_name=f"{colunas[0]}{target_row}:{colunas[1]}{target_row}",
                         values=[campos], value_input_option="RAW")
        time.sleep(0.3)
        limpar_cache()
        return True
    except Exception as e:
        st.error(f"Erro ao atualizar: {e}")
        return False


def excluir_pontos_lote(linhas_reais: list) -> bool:
    if not pode_executar_acao("2", "excluir"):
        st.error("Sem permissão para esta operação.")
        return False
    try:
        linhas_ordenadas = sorted(
            [r + 2 for r in linhas_reais],
            reverse=True
        )

        for r in linhas_ordenadas:
            worksheet.delete_rows(r)

        time.sleep(0.3)
        limpar_cache()

        return True

    except Exception as e:
        st.error(f"Erro ao excluir: {e}")
        return False


def data_para_str(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def gerar_kml(df):
    kml = simplekml.Kml()

    for _, row in df.iterrows():
        lat = row.get("Latitude")
        lon = row.get("Longitude")

        if pd.notnull(lat) and pd.notnull(lon):
            try:
                kml.newpoint(
                    name=str(row.get("Pontos", "Ponto")),
                    description=(
                        f"Data: {row.get('Data', '')}\n"
                        f"Município: {row.get('Município', '')}\n"
                        f"Ponto: {row.get('Pontos', '')}\n"
                        f"MCA: {row.get('MCA', '')}\n"
                        f"Observação: {row.get('Observacao', '')}"
                    ),
                    coords=[(float(lon), float(lat))]
                )

            except (ValueError, TypeError):
                continue

    return kml.kml()


# ============================================================
# SESSION STATE
# ============================================================
hoje = date.today()

if "data_inicial_selecionada" not in st.session_state:
    st.session_state.data_inicial_selecionada = hoje

if "data_final_selecionada" not in st.session_state:
    st.session_state.data_final_selecionada = hoje

if "clicked_lat" not in st.session_state:
    st.session_state.clicked_lat = None

if "clicked_lon" not in st.session_state:
    st.session_state.clicked_lon = None

if "modo_adicionar_mapa" not in st.session_state:
    st.session_state.modo_adicionar_mapa = False

if "dados_upload_pendentes" not in st.session_state:
    st.session_state.dados_upload_pendentes = None

if "nome_arquivo_pendente" not in st.session_state:
    st.session_state.nome_arquivo_pendente = None

if "file_uploader_key" not in st.session_state:
    st.session_state.file_uploader_key = 0


# ============================================================
# DIALOGS (POP-UPS DE CADASTRO, EDIÇÃO E PRÉ-VISUALIZAÇÃO DE UPLOAD)
# ============================================================
@st.dialog("➕ Cadastrar Ponto de Pressão")
def modal_novo_ponto():

    lat_default = (
        st.session_state.clicked_lat
        if st.session_state.clicked_lat is not None
        else 0.0
    )

    lon_default = (
        st.session_state.clicked_lon
        if st.session_state.clicked_lon is not None
        else 0.0
    )

    with st.form("form_novo_ponto_modal", clear_on_submit=True):

        data_cadastro = st.date_input(
            "Data do Registro",
            value=st.session_state.data_final_selecionada,
            format="DD/MM/YYYY"
        )

        municipio = st.text_input(
            "Município *",
            placeholder="Ex: Teresina"
        )

        pontos = st.text_input(
            "Pontos / Local *",
            placeholder="Ex: Ponto A-01"
        )

        c1, c2 = st.columns(2)

        with c1:
            lat = st.text_input(
                "Latitude * (aceita vírgula ou ponto)",
                value=str(lat_default)
            )

        with c2:
            lon = st.text_input(
                "Longitude * (aceita vírgula ou ponto)",
                value=str(lon_default)
            )

        mca = st.number_input(
            "MCA *",
            format="%.2f",
            value=0.00,
            min_value=0.0,
            step=0.1
        )

        obs = st.text_input(
            "Observação",
            placeholder="Ex: Válvula regulada"
        )

        enviado = st.form_submit_button(
            "Cadastrar Ponto",
            type="primary",
            use_container_width=True
        )

        if enviado:

            lat_n = normalizar_coordenada(lat, "lat")
            lon_n = normalizar_coordenada(lon, "lon")

            if not municipio.strip():
                st.error("O campo 'Município' é obrigatório.")

            elif not pontos.strip():
                st.error("O campo 'Pontos' é obrigatório.")

            elif lat_n is None or lon_n is None:
                st.error(
                    "Coordenadas inválidas. Verifique os valores de Latitude e Longitude."
                )

            else:
                qtd_inserida = adicionar_ponto(
                    data_para_str(data_cadastro),
                    municipio.strip(),
                    pontos.strip(),
                    lat_n,
                    lon_n,
                    mca,
                    obs.strip()
                )

                if qtd_inserida:
                    st.success("Ponto cadastrado com sucesso!")
                else:
                    st.info("Este ponto já foi registrado nesta data. Nenhuma duplicação foi feita.")

                st.session_state.clicked_lat = None
                st.session_state.clicked_lon = None

                st.rerun()


@st.dialog("✏️ Editar Ponto de Pressão")
def modal_editar_ponto(linha_real: int):

    df_all = carregar_dados()

    reg_edit = df_all[df_all["_linha_real"] == linha_real]

    if not reg_edit.empty:

        reg_edit = reg_edit.iloc[0]

        try:
            data_parsed = datetime.strptime(
                str(reg_edit["Data"]),
                "%d/%m/%Y"
            ).date()

        except ValueError:
            data_parsed = hoje

        with st.form("form_edicao_modal"):

            data_e = st.date_input(
                "Data do Registro",
                value=data_parsed,
                format="DD/MM/YYYY"
            )

            municipio_e = st.text_input(
                "Município *",
                value=str(reg_edit["Município"])
            )

            pontos_e = st.text_input(
                "Pontos *",
                value=str(reg_edit["Pontos"])
            )

            c1, c2 = st.columns(2)

            with c1:
                lat_e = st.text_input(
                    "Latitude *",
                    value=str(reg_edit["Latitude"] or LAT_BASE)
                )

            with c2:
                lon_e = st.text_input(
                    "Longitude *",
                    value=str(reg_edit["Longitude"] or LON_BASE)
                )

            mca_e = st.number_input(
                "MCA *",
                format="%.2f",
                value=float(reg_edit["MCA"] or 0.0),
                min_value=0.0,
                step=0.1
            )

            obs_e = st.text_input(
                "Observação",
                value=str(reg_edit["Observacao"])
            )

            col_salvar, col_canc = st.columns(2)

            with col_salvar:
                salvar_edicao = st.form_submit_button(
                    "💾 Salvar",
                    type="primary",
                    use_container_width=True
                )

            with col_canc:
                cancelar_edicao = st.form_submit_button(
                    "❌ Cancelar",
                    use_container_width=True
                )

            if salvar_edicao:

                lat_n = normalizar_coordenada(lat_e, "lat")
                lon_n = normalizar_coordenada(lon_e, "lon")

                if not municipio_e.strip():
                    st.error("O campo 'Município' é obrigatório.")

                elif lat_n is None or lon_n is None:
                    st.error("Coordenadas inválidas.")

                else:

                    if atualizar_ponto(
                        linha_real,
                        data_para_str(data_e),
                        municipio_e.strip(),
                        pontos_e,
                        lat_n,
                        lon_n,
                        mca_e,
                        obs_e
                    ):
                        st.success("Atualizado com sucesso!")
                        st.rerun()

            if cancelar_edicao:
                st.rerun()

    else:
        st.warning("Registro não encontrado.")


@st.dialog("📋 Pré-visualização da Planilha")
def modal_previa_upload():

    st.write(
        f"Arquivo carregado: **{st.session_state.nome_arquivo_pendente}**"
    )

    df_preview = pd.DataFrame(
        st.session_state.dados_upload_pendentes,
        columns=[
            "Data",
            "Município",
            "Pontos",
            "Latitude",
            "Longitude",
            "MCA",
            "Observacao"
        ]
    )

    st.dataframe(
        df_preview,
        use_container_width=True
    )

    st.info(
        f"Total de registros válidos prontos para envio: **{len(df_preview)}**"
    )

    col_btn1, col_btn2 = st.columns(2)

    with col_btn1:

        if st.button(
            "Confirmar e Enviar",
            type="primary",
            use_container_width=True
        ):

            with st.spinner(
                "Enviando registros com segurança para o Google Sheets..."
            ):
                qtd_inserida = adicionar_lote_seguro(
                    st.session_state.dados_upload_pendentes
                )

            st.session_state.dados_upload_pendentes = None
            st.session_state.nome_arquivo_pendente = None
            st.session_state.file_uploader_key += 1

            if qtd_inserida > 0:
                st.success(
                    f"✅ {qtd_inserida} novos registros importados com sucesso!"
                )
            else:
                st.info(
                    "ℹ️ Todos os registros da planilha já existiam no sistema. "
                    "Nenhuma duplicação foi feita."
                )

            time.sleep(1)
            st.rerun()

    with col_btn2:

        if st.button(
            "Cancelar",
            use_container_width=True
        ):
            st.session_state.dados_upload_pendentes = None
            st.session_state.nome_arquivo_pendente = None
            st.session_state.file_uploader_key += 1
            st.rerun()


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:

    st.markdown("### 🗺️ COI - Mapeamento")
    st.caption("⚙️ Painel Operacional")

    st.markdown("#### 📅 Selecionar Período")

    c_data_ini, c_data_fim = st.columns(2)

    with c_data_ini:
        data_inicial = st.date_input(
            "Data inicial",
            value=st.session_state.data_inicial_selecionada,
            format="DD/MM/YYYY",
            key="calendario_data_inicial"
        )

    with c_data_fim:
        data_final = st.date_input(
            "Data final",
            value=st.session_state.data_final_selecionada,
            format="DD/MM/YYYY",
            key="calendario_data_final"
        )

    st.session_state.data_inicial_selecionada = data_inicial
    st.session_state.data_final_selecionada = data_final

    periodo_valido = data_inicial <= data_final

    if not periodo_valido:

        st.error(
            "A data inicial não pode ser maior que a data final."
        )

    elif data_inicial == data_final:

        st.info(
            f"Exibindo dados de **{data_para_str(data_inicial)}**"
        )

    else:

        st.info(
            f"Exibindo dados de **{data_para_str(data_inicial)}** "
            f"a **{data_para_str(data_final)}**"
        )

    st.divider()

    st.markdown("#### 🔍 Filtros")

    df_all = carregar_dados()

    if not df_all.empty and periodo_valido:

        df_all["DataObjFiltro"] = pd.to_datetime(
            df_all["Data"],
            format="%d/%m/%Y",
            errors="coerce"
        )

        # Correção: comparação entre Timestamp e Timestamp
        data_inicial_dt = pd.Timestamp(data_inicial)
        data_final_dt = pd.Timestamp(data_final)

        df_data = df_all[
            (df_all["DataObjFiltro"] >= data_inicial_dt)
            & (df_all["DataObjFiltro"] <= data_final_dt)
        ].copy()

    else:

        df_data = (
            df_all.iloc[0:0].copy()
            if not df_all.empty
            else df_all
        )

    municipios_opts = (
        ["Todos"]
        + sorted(
            df_data["Município"].dropna().unique().tolist()
        )
        if not df_data.empty
        else ["Todos"]
    )

    municipio_sel = st.selectbox(
        "Município",
        municipios_opts,
        key="filtro_municipio"
    )

    df_data_mun = (
        df_data[df_data["Município"] == municipio_sel]
        if municipio_sel != "Todos"
        else df_data
    )

    pontos_opts = (
        ["Todos"]
        + sorted(
            df_data_mun["Pontos"].dropna().unique().tolist()
        )
        if not df_data_mun.empty
        else ["Todos"]
    )

    ponto_sel = st.selectbox(
        "Ponto",
        pontos_opts,
        key="filtro_ponto"
    )

    faixa_sel = st.selectbox(
        "Faixa de MCA",
        [
            "Todas",
            "Críticos (0 MCA)",
            "Atenção (≤ 5 MCA)",
            "Normais (> 5 MCA)"
        ],
        key="filtro_mca"
    )

    st.divider()

    st.markdown("#### ➕ Ações e Dados")

    if st.button(
        "Adicionar Novo Ponto",
        type="primary",
        use_container_width=True
    ):
        modal_novo_ponto()

    arquivo_upload = st.file_uploader(
        "📂 Enviar Planilha (XLSX/CSV)",
        type=["xlsx", "csv"],
        key=f"upload_mapeamento_{st.session_state.file_uploader_key}"
    )

    df_modelo = pd.DataFrame(
        [{
            "Data": datetime.now().strftime("%d/%m/%Y"),
            "Município": "Teresina",
            "Pontos": "Ponto Exemplo 01",
            "Latitude": -5.0892,
            "Longitude": -42.8019,
            "MCA": 12.5,
            "Observacao": "Exemplo de preenchimento"
        }],
        columns=COLUNAS_PADRAO
    )

    output_modelo = io.BytesIO()

    with pd.ExcelWriter(
        output_modelo,
        engine="openpyxl"
    ) as writer:

        df_modelo.to_excel(
            writer,
            index=False,
            sheet_name="Modelo"
        )

    st.download_button(
        label="📥 Baixar Planilha Modelo",
        data=output_modelo.getvalue(),
        file_name="modelo_importacao_pressao.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

    if arquivo_upload is not None:

        if (
            st.session_state.nome_arquivo_pendente
            != arquivo_upload.name
        ):

            try:

                if arquivo_upload.name.endswith(".csv"):

                    df_up = pd.read_csv(arquivo_upload)

                else:

                    df_up = pd.read_excel(
                        arquivo_upload,
                        engine="openpyxl"
                    )

                lote_para_enviar = []

                for _, row in df_up.iterrows():

                    raw_data = row.get(
                        "Data",
                        row.get("date", "")
                    )

                    data_val = normalizar_data(raw_data)

                    if not data_val:
                        data_val = data_para_str(data_final)

                    municipio_val = str(
                        row.get(
                            "Município",
                            row.get(
                                "Municipio",
                                row.get("cidade", "")
                            )
                        )
                    )

                    ponto_val = str(
                        row.get(
                            "Pontos",
                            row.get("Ponto", "")
                        )
                    )

                    lat_val = normalizar_coordenada(
                        row.get(
                            "Latitude",
                            row.get("Lat")
                        ),
                        "lat"
                    )

                    lon_val = normalizar_coordenada(
                        row.get(
                            "Longitude",
                            row.get("Lon")
                        ),
                        "lon"
                    )

                    mca_val = parse_float(
                        row.get(
                            "MCA",
                            row.get("Pressao", 0.0)
                        ),
                        0.0
                    )

                    obs_bruta = row.get("Observação", row.get("Observacao", ""))
                    obs_val = "" if pd.isna(obs_bruta) else str(obs_bruta).strip()

                    if (
                        municipio_val.strip()
                        and ponto_val.strip()
                        and lat_val is not None
                        and lon_val is not None
                    ):

                        lote_para_enviar.append([
                            data_val,
                            municipio_val.strip(),
                            ponto_val.strip(),
                            float(lat_val),
                            float(lon_val),
                            float(mca_val),
                            obs_val
                        ])

                if lote_para_enviar:

                    st.session_state.dados_upload_pendentes = (
                        lote_para_enviar
                    )

                    st.session_state.nome_arquivo_pendente = (
                        arquivo_upload.name
                    )

                else:

                    st.warning(
                        "⚠️ Nenhum registro válido encontrado. "
                        "Verifique se os nomes das colunas são: "
                        "Data, Município, Pontos, Latitude, Longitude, "
                        "MCA, Observação."
                    )

            except Exception as e:

                st.error(
                    f"❌ Erro ao processar arquivo: {e}"
                )

    if st.session_state.dados_upload_pendentes is not None:
        modal_previa_upload()

    st.divider()

    st.markdown("#### 📥 Exportar Dados")

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        if not df_all.empty:

            df_all[
                COLUNAS_PADRAO
            ].to_excel(
                writer,
                index=False,
                sheet_name="Mapeamento_Pressao"
            )

        else:

            pd.DataFrame(
                columns=COLUNAS_PADRAO
            ).to_excel(
                writer,
                index=False,
                sheet_name="Mapeamento_Pressao"
            )

    excel_data = output.getvalue()

    st.download_button(
        label="📊 Baixar em Excel (XLSX)",
        data=excel_data,
        file_name=(
            f"mapeamento_pressao_"
            f"{datetime.now().strftime('%Y%m%d')}.xlsx"
        ),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

    kml_string = (
        gerar_kml(df_all)
        if not df_all.empty
        else simplekml.Kml().kml()
    )

    st.download_button(
        label="🗺️ Baixar Mapa (KML/KMZ)",
        data=kml_string,
        file_name=(
            f"mapeamento_pressao_"
            f"{datetime.now().strftime('%Y%m%d')}.kml"
        ),
        mime="application/vnd.google-earth.kml+xml",
        use_container_width=True
    )

    st.divider()

    st.markdown("#### ⏱️ Atualização")

    intervalo = st.select_slider(
        "Intervalo (segundos)",
        options=[0, 15, 30, 60, 120],
        value=60
    )

    if intervalo > 0:
        st_autorefresh(
            interval=intervalo * 1000,
            key="autorefresh"
        )

    st.markdown(
        "<br>" * 2,
        unsafe_allow_html=True
    )

    st.divider()

    if st.button(
        "🏠 Voltar ao Menu Principal",
        use_container_width=True
    ):
        st.switch_page("app.py")

    st.divider()

    if "modo_escuro_mp" not in st.session_state:
        st.session_state.modo_escuro_mp = False

    modo_escuro_mp = st.toggle(
        "🌙 Modo escuro",
        value=st.session_state.modo_escuro_mp,
        key="toggle_modo_escuro_mp",
    )

    st.session_state.modo_escuro_mp = modo_escuro_mp

    if modo_escuro_mp:

        st.markdown(
            """
            <style>
                .stApp {
                    background-color: #0e1117;
                    color: #fafafa;
                }

                [data-testid="stSidebar"] {
                    background-color: #161b22;
                }

                .stApp p,
                .stApp label,
                .stApp h1,
                .stApp h2,
                .stApp h3,
                .stApp h4,
                .stApp h5,
                .stApp h6 {
                    color: #f0f0f0 !important;
                }

                [data-testid="stMetric"],
                [data-testid="stExpander"],
                div[data-testid="stDataFrame"] {
                    background-color: #161b22;
                }

                input,
                textarea,
                [data-baseweb="select"] > div {
                    background-color: #21262d !important;
                    color: #f0f0f0 !important;
                }

                [data-baseweb="select"] * {
                    color: #f0f0f0 !important;
                }

                /* Botões normais e botões de download */
                .stButton > button,
                .stDownloadButton > button {
                    background-color: #000000 !important;
                    color: #ffffff !important;
                    border: 1px solid #444c56 !important;
                }

                /* Texto interno dos botões */
                .stButton > button *,
                .stDownloadButton > button * {
                    color: #ffffff !important;
                }

                /* Efeito ao passar o mouse */
                .stButton > button:hover,
                .stDownloadButton > button:hover {
                    background-color: #000000 !important;
                    color: #ff0000 !important;
                    border-color: #ff0000 !important;
                }

                /* Texto interno no hover */
                .stButton > button:hover *,
                .stDownloadButton > button:hover * {
                    color: #ff0000 !important;
                }

                [data-testid="stDataFrame"] {
                    color: #f0f0f0;
                }
            </style>
            """,
            unsafe_allow_html=True,
        )


# ============================================================
# ÁREA PRINCIPAL
# ============================================================
st.title("🗺️ Painel de Mapeamento de Pressão - COI")

if periodo_valido:

    if data_inicial == data_final:

        st.caption(
            f"Visualizando dados da data: "
            f"**{data_para_str(data_inicial)}**"
        )

    else:

        st.caption(
            f"Visualizando dados do período: "
            f"**{data_para_str(data_inicial)} "
            f"a {data_para_str(data_final)}**"
        )

else:

    st.caption(
        "Período inválido. Ajuste as datas para visualizar os dados."
    )


df = carregar_dados()

if not df.empty and periodo_valido:

    df["DataObjFiltro"] = pd.to_datetime(
        df["Data"],
        format="%d/%m/%Y",
        errors="coerce"
    )

    # Correção: comparação entre Timestamp e Timestamp
    data_inicial_dt = pd.Timestamp(data_inicial)
    data_final_dt = pd.Timestamp(data_final)

    df_filtrado = df[
        (df["DataObjFiltro"] >= data_inicial_dt)
        & (df["DataObjFiltro"] <= data_final_dt)
    ].copy()

else:

    df_filtrado = (
        df.iloc[0:0].copy()
        if not df.empty
        else df.copy()
    )


if municipio_sel != "Todos":
    df_filtrado = df_filtrado[
        df_filtrado["Município"] == municipio_sel
    ]


if ponto_sel != "Todos":
    df_filtrado = df_filtrado[
        df_filtrado["Pontos"] == ponto_sel
    ]


if faixa_sel == "Críticos (0 MCA)":

    df_filtrado = df_filtrado[
        df_filtrado["MCA"] == 0
    ]

elif faixa_sel == "Atenção (≤ 5 MCA)":

    df_filtrado = df_filtrado[
        (df_filtrado["MCA"] > 0)
        & (df_filtrado["MCA"] <= 5)
    ]

elif faixa_sel == "Normais (> 5 MCA)":

    df_filtrado = df_filtrado[
        df_filtrado["MCA"] > 5
    ]


if not df_filtrado.empty:

    total = len(df_filtrado)

    criticos = len(
        df_filtrado[
            df_filtrado["MCA"] == 0
        ]
    )

    atencao = len(
        df_filtrado[
            (df_filtrado["MCA"] > 0)
            & (df_filtrado["MCA"] <= 5)
        ]
    )

    normais = len(
        df_filtrado[
            df_filtrado["MCA"] > 5
        ]
    )

    k1, k2, k3, k4 = st.columns(4)

    k1.metric(
        "Total de Ocorrências",
        total
    )

    k2.metric(
        "Críticos (0 MCA)",
        criticos
    )

    k3.metric(
        "Em Atenção (≤ 5 MCA)",
        atencao
    )

    k4.metric(
        "Normais (> 5 MCA)",
        normais
    )

else:

    if periodo_valido:

        if data_inicial == data_final:

            st.info(
                f"Nenhum ponto registrado para a data "
                f"{data_para_str(data_inicial)} "
                f"com os filtros selecionados."
            )

        else:

            st.info(
                f"Nenhum ponto registrado no período de "
                f"{data_para_str(data_inicial)} "
                f"a {data_para_str(data_final)} "
                f"com os filtros selecionados."
            )

    else:

        st.info(
            "Ajuste o período selecionado para visualizar os dados."
        )


st.divider()


# ============================================================
# MAPA COM SELETOR DE TIPO DE MAPA
# ============================================================
st.subheader("🗺️ Mapa de Mapeamento de Pressão")

c_map1, c_map2, c_map3 = st.columns([2, 2, 2])

with c_map1:

    tipo_mapa = st.selectbox(
        "🗺️ Tipo de Mapa",
        options=[
            "Mapa Padrão (OpenStreetMap)",
            "Satélite (Esri World Imagery)",
            "Terreno (OpenTopoMap)"
        ],
        key="seletor_tipo_mapa"
    )


with c_map2:

    mostrar_rotulos = st.checkbox(
        "Exibir rótulos",
        value=False
    )


with c_map3:

    st.session_state.modo_adicionar_mapa = st.checkbox(
        "📍 Modo adicionar ponto",
        value=st.session_state.modo_adicionar_mapa
    )


if (
    not df_filtrado.empty
    and df_filtrado["Latitude"].notna().any()
):

    centro_lat = float(
        df_filtrado["Latitude"].mean()
    )

    centro_lon = float(
        df_filtrado["Longitude"].mean()
    )

    zoom = 13

else:

    centro_lat = LAT_BASE
    centro_lon = LON_BASE
    zoom = 12


m = folium.Map(
    location=[centro_lat, centro_lon],
    zoom_start=zoom,
    tiles=None
)


if "Satélite" in tipo_mapa:

    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/"
            "ArcGIS/rest/services/World_Imagery/"
            "MapServer/tile/{z}/{y}/{x}"
        ),
        attr=(
            "Esri &mdash; Source: Esri, i-cubed, USDA, "
            "USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, "
            "IGP, UPR-EGP, and the GIS User Community"
        ),
        name="Satélite (Esri World Imagery)"
    ).add_to(m)


elif "Terreno" in tipo_mapa:

    folium.TileLayer(
        "OpenTopoMap",
        name="Terreno (OpenTopoMap)"
    ).add_to(m)


else:

    folium.TileLayer(
        "OpenStreetMap",
        name="Mapa Padrão (OpenStreetMap)"
    ).add_to(m)


if not df_filtrado.empty:

    validos = df_filtrado.dropna(
        subset=["Latitude", "Longitude"]
    )

    for _, row in validos.iterrows():

        mca = row["MCA"]

        cor = (
            "red"
            if mca == 0
            else (
                "orange"
                if mca <= 5
                else "blue"
            )
        )

        popup = (
            f"<b>Data:</b> {row['Data']}<br>"
            f"<b>Município:</b> {row['Município']}<br>"
            f"<b>Ponto:</b> {row['Pontos']}<br>"
            f"<b>MCA:</b> {mca}<br>"
            f"<b>Obs:</b> {row['Observacao']}"
        )

        marker_icon = folium.Icon(
            color=cor,
            icon="map-pin",
            prefix="fa"
        )

        folium.Marker(
            location=[
                row["Latitude"],
                row["Longitude"]
            ],
            popup=folium.Popup(
                popup,
                max_width=250
            ),
            tooltip=f"{row['Pontos']} ({mca} MCA)",
            icon=marker_icon
        ).add_to(m)

        if mostrar_rotulos:

            texto_rotulo = (
                f"{row['Pontos']} — {mca} MCA"
            )

            folium.map.Marker(
                [
                    row["Latitude"],
                    row["Longitude"]
                ],
                icon=folium.DivIcon(
                    icon_size=(200, 40),
                    icon_anchor=(-12, 18),
                    html=f'''
                    <div style="
                        font-family: sans-serif;
                        font-size: 11px;
                        font-weight: 600;
                        color: #1f2937;
                        background-color: rgba(255, 255, 255, 0.92);
                        padding: 4px 8px;
                        border-radius: 6px;
                        border: 1px solid #cbd5e1;
                        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
                        width: max-content;
                        white-space: nowrap;
                    ">
                        📍 {texto_rotulo}
                    </div>
                    '''
                )
            ).add_to(m)


map_data = st_folium(
    m,
    width="100%",
    height=520,
    returned_objects=["last_clicked"],
    key="mapa_principal_map"
)


if (
    st.session_state.modo_adicionar_mapa
    and map_data
    and map_data.get("last_clicked")
):

    clicked = map_data["last_clicked"]

    if clicked:

        st.session_state.clicked_lat = round(
            clicked["lat"],
            6
        )

        st.session_state.clicked_lon = round(
            clicked["lng"],
            6
        )

        modal_novo_ponto()


st.divider()


# ============================================================
# TABELA E GERENCIAMENTO
# (EDIÇÃO E EXCLUSÃO MÚLTIPLA COM CHAVE REAL)
# ============================================================
st.subheader("📋 Registro de Pontos Mapeados")

if not df_filtrado.empty:

    # Prepara o dataframe mantendo a coluna oculta
    # _linha_real para rastreamento exato
    df_show = df_filtrado[
        COLUNAS_PADRAO + ["_linha_real"]
    ].copy()

    df_show.insert(
        0,
        "Selecionar",
        False
    )

    edited_df = st.data_editor(
        df_show,
        use_container_width=True,
        height=350,
        disabled=COLUNAS_PADRAO + ["_linha_real"],
        key="editor_tabela_map_unificado",
        hide_index=True,
    )

    # ===== EXCLUSÃO =====
    selecionados = edited_df[
        edited_df["Selecionar"] == True
    ]

    c_edit, c_del = st.columns([2, 2])

    with c_edit:

        # ===== EDIÇÃO mais segura =====
        # Cria opções únicas e legíveis
        opcoes_map = {}
        opcoes_lista = [
            "-- Selecione o Ponto --"
        ]

        for _, row in df_filtrado.iterrows():

            label = (
                f"{row['Pontos']}  |  "
                f"{row['Município']}  |  "
                f"MCA: {row['MCA']}"
            )

            # garante unicidade mesmo com nomes repetidos
            while label in opcoes_map:
                label += " "

            opcoes_lista.append(label)

            opcoes_map[label] = int(
                row["_linha_real"]
            )

        ponto_para_editar = st.selectbox(
            "Selecionar Ponto para Editar",
            options=opcoes_lista,
            key="select_edicao_ponto_preciso"
        )

        if (
            ponto_para_editar
            != "-- Selecione o Ponto --"
        ):

            if st.button(
                "✏️ Abrir Edição do Ponto Selecionado",
                type="primary",
                use_container_width=True
            ):

                linha_real_alvo = opcoes_map[
                    ponto_para_editar
                ]

                modal_editar_ponto(
                    linha_real_alvo
                )

    with c_del:

        st.markdown(
            "<br>",
            unsafe_allow_html=True
        )

        if not selecionados.empty:

            if st.button(
                f"🗑️ Excluir Selecionados "
                f"({len(selecionados)})",
                type="primary",
                use_container_width=True
            ):

                # Agora pega diretamente a coluna
                # _linha_real
                linhas_reais_para_excluir = (
                    selecionados["_linha_real"]
                    .astype(int)
                    .tolist()
                )

                if excluir_pontos_lote(
                    linhas_reais_para_excluir
                ):

                    st.success(
                        f"🗑️ "
                        f"{len(linhas_reais_para_excluir)} "
                        f"registro(s) excluído(s) "
                        f"com sucesso!"
                    )

                    st.rerun()

        else:

            st.button(
                "🗑️ Excluir Selecionados",
                disabled=True,
                use_container_width=True
            )

else:

    if periodo_valido:

        if data_inicial == data_final:

            st.info(
                f"Nenhum ponto registrado para a data "
                f"{data_para_str(data_inicial)} "
                f"com os filtros selecionados."
            )

        else:

            st.info(
                f"Nenhum ponto registrado no período de "
                f"{data_para_str(data_inicial)} "
                f"a {data_para_str(data_final)} "
                f"com os filtros selecionados."
            )

    else:

        st.info(
            "Ajuste o período selecionado para visualizar os dados."
        )

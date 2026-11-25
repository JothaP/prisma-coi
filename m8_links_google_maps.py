"""Extração de coordenadas de links do Google Maps (Módulo 8)."""
from __future__ import annotations

import math
import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests

DOMINIOS = {
    "www.google.com", "google.com", "maps.google.com",
    "www.google.com.br", "google.com.br", "maps.google.com.br",
    "maps.app.goo.gl", "goo.gl",
}
CURTOS = {"maps.app.goo.gl", "goo.gl"}
NUM = r"[+-]?\d{1,3}(?:\.\d{1,12})?"
PAR = re.compile(rf"(?P<lat>{NUM})\s*,\s*(?P<lon>{NUM})")
LOCAL = re.compile(rf"!3d(?P<lat>{NUM})!4d(?P<lon>{NUM})")
CENTRO = re.compile(rf"@(?P<lat>{NUM}),(?P<lon>{NUM})(?=,|/|$)")


def validar_url(link: str) -> tuple[str, str]:
    url = str(link or "").strip()
    if not url or len(url) > 4096:
        raise ValueError("Informe um link de até 4096 caracteres.")
    if "://" not in url:
        url = "https://" + url
    try:
        parsed = urlsplit(url)
        host, port = (parsed.hostname or "").lower().rstrip("."), parsed.port
    except ValueError as exc:
        raise ValueError("Link inválido.") from exc
    if (parsed.scheme != "https" or host not in DOMINIOS or port is not None
            or parsed.username or parsed.password):
        raise ValueError("Aceitos apenas links HTTPS oficiais do Google Maps.")
    if host == "goo.gl" and not parsed.path.startswith("/maps/"):
        raise ValueError("O encurtador goo.gl deve apontar para /maps/.")
    if host not in CURTOS and not (
        parsed.path.startswith("/maps") or host.startswith("maps.google.")
        or parsed.path in ("", "/")
    ):
        raise ValueError("O endereço não corresponde ao Google Maps.")
    return url, host


def par_valido(lat: str, lon: str) -> tuple[float, float] | None:
    try:
        a, b = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    return (a, b) if math.isfinite(a) and math.isfinite(b) and -90 <= a <= 90 and -180 <= b <= 180 else None


def obter_par(valor: str) -> tuple[float, float] | None:
    achado = PAR.fullmatch(re.sub(r"^loc:\s*", "", unquote(valor).strip(), flags=re.I))
    return par_valido(achado.group("lat"), achado.group("lon")) if achado else None


def interpretar_url(link: str, aceitar_centro: bool = False):
    link, _ = validar_url(link)
    parsed = urlsplit(link)
    path = unquote(parsed.path)
    query = parse_qs(parsed.query)
    if re.search(r"^/(?:maps/)?dir(?:/|$)", path, flags=re.I) or (
        "origin" in query and "destination" in query
    ):
        raise ValueError("Links de rotas não identificam um único local.")
    # Coordenada do marcador prevalece sobre coordenada do centro da camera.
    for achado in reversed(list(LOCAL.finditer(unquote(link)))):
        par = par_valido(achado.group("lat"), achado.group("lon"))
        if par is not None:
            return par, "Local marcado"
    for chave in ("q", "query"):
        for item in query.get(chave, []):
            par = obter_par(item)
            if par is not None:
                return par, "Busca por coordenadas"
    achado = re.match(r"^/maps/(?:place|search)/", path, flags=re.I)
    if achado:
        par = PAR.match(path[achado.end():])
        if par:
            coords = par_valido(par.group("lat"), par.group("lon"))
            if coords is not None:
                return coords, "Coordenada do link"
    if aceitar_centro:
        for chave in ("ll", "center"):
            for item in query.get(chave, []):
                par = obter_par(item)
                if par is not None:
                    return par, "Centro do mapa"
        achado = CENTRO.search(unquote(link))
        if achado:
            par = par_valido(achado.group("lat"), achado.group("lon"))
            if par is not None:
                return par, "Centro do mapa"
    return None


def resolver_link_curto(url: str, sessao: requests.Session) -> str:
    """Segue somente redirecionamentos HTTPS para dominios Google autorizados."""
    vistos = set()
    for _ in range(7):
        url, host = validar_url(url)
        if url in vistos:
            raise ValueError("Redirecionamento circular.")
        vistos.add(url)
        with sessao.get(url, allow_redirects=False, stream=True, timeout=(3, 5)) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    raise ValueError("O link curto não informou o destino.")
                proximo = urljoin(url, location)
                validar_url(proximo)
                url = proximo
                continue
            if resp.status_code >= 400:
                raise ValueError(f"Google Maps retornou HTTP {resp.status_code}.")
            if host in CURTOS:
                raise ValueError("O link curto não retornou redirecionamento reconhecível.")
            return url
    raise ValueError("Redirecionamentos demais neste link.")


def extrair_link(link: str, aceitar_centro: bool = False, sessao=None):
    url, host = validar_url(link)
    if host in CURTOS:
        cliente = sessao if sessao is not None else requests.Session()
        try:
            url = resolver_link_curto(url, cliente)
        except requests.RequestException as exc:
            raise ValueError("Não foi possível acessar o link curto.") from exc
        finally:
            if sessao is None:
                cliente.close()
    resultado = interpretar_url(url, aceitar_centro=aceitar_centro)
    if resultado is None:
        raise ValueError("Sem coordenada do local; opcionalmente aceite o centro do mapa.")
    return resultado


def extrair_lista_links(texto: str, aceitar_centro: bool = False, sessao=None) -> list[dict]:
    linhas = [(numero, linha.strip()) for numero, linha in
              enumerate(str(texto or "").splitlines(), 1) if linha.strip()]
    if not linhas:
        raise ValueError("Cole ao menos um link do Google Maps.")
    if len(linhas) > 100:
        raise ValueError("Consulte no máximo 100 links por pesquisa.")
    cliente = sessao if sessao is not None else requests.Session()
    resultado = []
    try:
        for numero, link in linhas:
            try:
                coords, origem = extrair_link(link, aceitar_centro, cliente)
                resultado.append({"linha": numero, "status": "OK",
                                  "coordenadas": coords, "origem": origem, "erro": ""})
            except ValueError as exc:
                resultado.append({"linha": numero, "status": "ERRO",
                                  "coordenadas": None, "origem": "", "erro": str(exc)})
    finally:
        if sessao is None:
            cliente.close()
    contador = 0
    for item in resultado:
        if item["status"] == "OK":
            contador += 1
            item["ponto"] = f"P{contador}"
        else:
            item["ponto"] = "—"
    return resultado

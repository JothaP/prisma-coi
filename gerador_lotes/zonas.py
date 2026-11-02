import unicodedata
import pandas as pd


# ============================================================
# MAPA DE ZONAS
# ============================================================

MAPA_ZONAS = {
    "ACAUA": 229,
    "AGRICOLANDIA": 1,
    "AGUA BRANCA": 3,
    "ALAGOINHA": 129,
    "ALEGRETE DO PIAUI": 439,
    "ALTO LONGA": 2,
    "ALTOS": 4,
    "ALVORADA DO GURGUEIA": 414,
    "AMARANTE": 5,
    "ANGICAL DO PIAUI": 6,
    "ANISIO DE ABREU": 7,
    "ANTONIO ALMEIDA": 474,
    "AROAZES": 10,
    "AROEIRAS DO ITAIM": 415,
    "ARRAIAL": 9,
    "ASSUNCAO DO PIAUI": 422,
    "AVELINO LOPES": 11,
    "BAIXA GRANDE DO RIBEIRO": 125,
    "BARRA D ALCANTARA": 423,
    "BARRAS": 12,
    "BARREIRAS DO PIAUI": 13,
    "BARRO DURO": 14,
    "BATALHA": 15,
    "BELA VISTA DO PIAUI": 438,
    "BELEM DO PIAUI": 318,
    "BENEDITINOS": 19,
    "BERTOLINIA": 17,
    "BETANIA DO PIAUI": 441,
    "BOA HORA": 416,
    "BOCAINA": 16,
    "BOM JESUS": 20,
    "BOM PRINCIPIO DO PIAUI": 442,
    "BONFIM DO PIAUI": 179,
    "BOQUEIRAO DO PIAUI": 401,
    "BRASILEIRA": 121,
    "BREJO DO PIAUI": 160,
    "BURITI DOS LOPES": 18,
    "BURITI DOS MONTES": 443,
    "CABECEIRAS DO PIAUI": 127,
    "CAJAZEIRAS DO PIAUI": 444,
    "CAJUEIRO DA PRAIA": 206,
    "CALDEIRAO GRANDE DO PIAUI": 417,
    "CAMPINAS DO PIAUI": 22,
    "CAMPO ALEGRE DO FIDALGO": 418,
    "CAMPO GRANDE DO PIAUI": 195,
    "CAMPO LARGO DO PIAUI": 419,

    # CAMPO MAIOR NÃO POSSUI ZONA DEFINIDA

    "CANAVIEIRA": 138,
    "CANTO DO BURITI": 24,
    "CAPITAO DE CAMPOS": 21,
    "CAPITAO GERVASIO OLIVEIRA": 429,
    "CARACOL": 25,
    "CARAUBAS DO PIAUI": 402,
    "CARIDADE": 322,
    "CASTELO DO PIAUI": 27,
    "CAXINGO": 446,
    "COCAL": 28,
    "COCAL DE TELHA": 403,
    "COCAL DOS ALVES": 447,
    "COIVARAS": 448,
    "COLONIA DO GURGUEIA": 123,
    "COLONIA DO PIAUI": 162,
    "CONCEICAO DO CANINDE": 26,
    "CORONEL JOSE DIAS": 270,
    "CORRENTE": 29,
    "CRISTALANDIA": 30,
    "CRISTINO CASTRO": 31,
    "CURIMATA": 32,
    "CURRAIS": 449,
    "CURRAL NOVO PI": 268,
    "CURRALINHOS": 412,
    "DEMERVAL LOBAO": 33,
    "DIRCEU ARCOVERDE": 115,
    "DOM EXPEDITO LOPES": 34,
    "DOM INOCENCIO": 400,
    "DOMINGOS MOURAO": 70,
    "ELESBAO VELOSO": 36,
    "ELIZEU MARTINS": 35,
    "ESPERANTINA": 37,
    "FARTURA DO PIAUI": 329,
    "FLORES DO PIAUI": 39,
    "FLORESTA DO PIAUI": 450,
    "FLORIANO": 41,
    "FRANCINOPOLIS": 38,
    "FRANCISCO AIRES": 40,
    "FRANCISCO MACEDO": 404,
    "FRANCISCO SANTOS": 42,
    "FRONTEIRAS": 43,
    "GEMINIANO": 433,
    "GILBUES": 44,
    "GUADALUPE": 45,
    "GUARIBAS": 281,
    "HUGO NAPOLEAO": 46,
    "ILHA GRANDE": 149,
    "INHUMA": 47,
    "IPIRANGA": 49,
    "ISAIAS COELHO": 50,
    "ITAINOPOLIS": 51,
    "ITAUEIRA": 48,
    "JACOBINA DO PIAUI": 192,
    "JAICOS": 52,
    "JARDIM MULATO": 136,
    "JATOBA DO PIAUI": 452,
    "JERUMENHA": 54,
    "JOAO COSTA": 453,
    "JOAQUIM PIRES": 55,
    "JOCA MARQUES": 454,
    "JOSE DE FREITAS": 53,
    "JUAZEIRO DO PIAUI": 214,
    "JULIO BORGES": 203,
    "JUREMA": 209,
    "LAGOA ALEGRE": 120,
    "LAGOA DE SAO FRANCISCO": 424,
    "LAGOA DO BARRO DO PIAUI": 176,
    "LAGOA DO PIAUI": 455,
    "LAGOA DO SITIO": 425,
    "LAGOINHA DO PIAUI": 456,
    "LANDRI SALES": 435,
    "LUIS CORREIA": 57,
    "LUZILANDIA": 58,
    "MADEIRO": 457,
    "MANOEL EMIDIO": 59,
    "MARCOLANDIA": 406,
    "MARCOS PARENTE": 60,
    "MASSAPE DO PIAUI": 431,
    "MATIAS OLIMPIO": 61,
    "MIGUEL ALVES": 62,
    "MIGUEL LEAO": 459,
    "MILTON BRANDAO": 473,
    "MONSENHOR GIL": 65,
    "MONSENHOR HIPOLITO": 66,
    "MONTE ALEGRE": 64,
    "MORRO CABECA NO TEMPO": 434,
    "MORRO DO CHAPEU DO PIAUI": 405,
    "MURICI DOS PORTELAS": 320,
    "NAZARE DO PIAUI": 67,
    "NAZARIA": 131,
    "NOSSA SENHORA DE NAZARE": 420,
    "NOSSA SRA DOS REMEDIOS": 69,
    "NOVA SANTA RITA": 312,
    "NOVO ORIENTE DO PIAU": 68,
    "NOVO SANTO ANTONIO": 410,
    "OEIRAS": 71,
    "OLHO D'AGUA DO PIAUI": 461,
    "PADRE MARCOS": 72,
    "PAES LANDIM": 73,
    "PAJEU DO PIAUI": 462,
    "PALMEIRA DO PIAUI": 74,
    "PALMEIRAIS": 75,
    "PAQUETA": 187,
    "PARNAGUA": 76,
    "PARNAIBA": 77,
    "PASSAGEM FRANCA": 130,
    "PATOS DO PIAUI": 310,
    "PAU D ARCO DO PIAUI": 436,
    "PAULISTANA": 79,
    "PAVUSSU": 135,
    "PEDRO II": 80,
    "PEDRO LAURENTINO": 428,
    "PICOS": 81,
    "PIMENTEIRAS": 82,
    "PIO IX": 78,
    "PIRACURUCA": 83,
    "PIRIPIRI": 84,
    "PORTO": 85,
    "PORTO ALEGRE DO PIAUI": 426,
    "PRATA DO PIAUI": 86,
    "QUEIMADA NOVA": 465,
    "REDENCAO DO GURGUEIA": 466,
    "REGENERACAO": 89,
    "RIACHO FRIO": 146,
    "RIBEIRA DO PIAUI": 467,
    "RIBEIRO GONCALVES": 88,
    "RIO GRANDE DO PIAUI": 90,
    "SANTA CRUZ DO PIAUI": 93,
    "SANTA CRUZ DOS MILAGRES": 468,
    "SANTA FILOMENA": 94,
    "SANTA LUZ": 96,
    "SANTA ROSA DO PIAUI": 117,
    "SANTA TERESA": 158,
    "SANTANA DO PIAUI": 212,
    "SANTO ANTONIO DE LISBOA": 91,
    "SANTO ANTONIO D MILA": 122,
    "SANTO INACIO DO PIAUI": 102,
    "SAO BRAZ": 319,
    "SAO FELIX": 95,
    "SAO FRANCISCO DE ASSIS": 263,
    "SAO FRANCISCO DO PIAUI": 97,
    "SAO GONCALO DO GURGUEIA": 469,
    "SAO GONCALO DO PIAUI": 98,
    "SAO JOAO DA CANABRAVA": 116,
    "SAO JOAO DA FRONTEIRA": 408,
    "SAO JOAO DA SERRA": 99,
    "SAO JOAO DA VARJOTA": 411,
    "SAO JOAO DO ARRAIAL": 470,
    "SAO JOAO DO PIAUI": 104,
    "SAO JOSE DO DIVINO": 174,
    "SAO JOSE DO PEIXE": 103,
    "SAO JOSE DO PIAUI": 92,
    "SAO JULIAO": 100,
    "SAO LOURENCO": 269,
    "SAO LUIS DO PIAUI": 202,
    "SAO MIGUEL DA BAIXA GRANDE": 427,
    "SAO MIGUEL DO FIDALGO": 432,
    "SAO MIGUEL TAPUIO": 101,
    "SAO PEDRO": 105,
    "SAO RAIMUNDO NONATO": 106,
    "SEBASTIAO BARROS": 472,
    "SEBASTIAO LEAL": 126,
    "SIGEFREDO PACHECO": 409,
    "SIMOES": 107,
    "SIMPLICIO MENDES": 108,
    "SOCORRO DO PIAUI": 109,
    "SUSSUAPARA": 421,
    "TAMBORIL DO PIAUI": 437,
    "TANQUE DO PIAUI": 430,
    "TERESINA": 110,
    "UNIAO": 111,
    "URUCUI": 112,
    "VALENCA": 113,
    "VARZEA BRANCA": 193,
    "VARZEA GRANDE": 114,
    "VERA MENDES": 413,
    "VILA NOVA DO PIAUI": 196,
    "WALL FERRAZ": 309,

    "POV SANTA TERESA": 158,
    "POV CALDEIRAOZINHO": 287,
    "POVOADO BURITIZINHO": 233,
    "POV COROA DE SAO REMIGIO": 277,
    "POVOADO PEDRA": 157,
    "POVOADO APARECIDA": 143,
    "POV BARRA DO LONGA": 124,
    "POV INGAZEIRA": 330,
    "POV SERRA DA SOLTA": 302,
    "POVOADO BARRA GRANDE": 239,
    "POVOADO SAO JOAQUIM": 210,
    "POVOADO TRANQUEIRA": 235,
    "POV MOCAMBINHO": 148,
    "POV BURITI DO CASTELO": 189,
    "POVOADO MANDACARU": 288,
    "POVOADO MATINHA": 183,
    "POV DAVID CALDAS": 119,
    "POV. LAGOA DE BAIXO": 331,
}


# ============================================================
# NORMALIZAÇÃO
# ============================================================

def normalizar_texto(texto):

    if pd.isna(texto):
        return ""

    texto = str(texto)

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(caractere)
    )

    texto = texto.upper().strip()

    while "  " in texto:
        texto = texto.replace(
            "  ",
            " ",
        )

    return texto


# ============================================================
# MAPA NORMALIZADO
# ============================================================

MAPA_ZONAS_NORMALIZADO = {
    normalizar_texto(cidade): zona
    for cidade, zona in MAPA_ZONAS.items()
}


# ============================================================
# OBTÉM ZONA
# ============================================================

def obter_zona(cidade):

    cidade_normalizada = normalizar_texto(
        cidade
    )

    if not cidade_normalizada:
        return None

    return MAPA_ZONAS_NORMALIZADO.get(
        cidade_normalizada
    )

"""
Función B — Descripciones rotativas (plantillas con variables).
Las plantillas se editan en la web app; la extensión de Chrome las rellena dentro de YouTube Studio.
Sin IA: texto fijo + variables. La "frase del día" se escribe una vez y sale en los 12 signos.
"""
from datetime import datetime, timedelta, timezone
import re

from pydantic import BaseModel

from main import *
from models import OwnChannel, RotatingTemplate, Setting
import youtube_api as yt

router = APIRouter(prefix="/templates", tags=["templates"])

# ───────────────────────── Signos y fechas por idioma ─────────────────────────
SIGNS = {
    "es": ["Aries", "Tauro", "Géminis", "Cáncer", "Leo", "Virgo", "Libra", "Escorpio", "Sagitario", "Capricornio", "Acuario", "Piscis"],
    "en": ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"],
    "pt": ["Áries", "Touro", "Gêmeos", "Câncer", "Leão", "Virgem", "Libra", "Escorpião", "Sagitário", "Capricórnio", "Aquário", "Peixes"],
}
SIGN_EMOJI = ["♈️", "♉️", "♊️", "♋️", "♌️", "♍️", "♎️", "♏️", "♐️", "♑️", "♒️", "♓️"]
MONTHS = {
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "pt": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"],
}
WEEKDAYS = {
    "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "pt": ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"],
}
# Hora local de referencia para "hoy" (República Dominicana, UTC-4)
LOCAL_UTC_OFFSET_HOURS = -4


def local_today() -> datetime:
    return (datetime.now(timezone.utc) + timedelta(hours=LOCAL_UTC_OFFSET_HOURS)).replace(tzinfo=None)


def build_variables(language: str, sign_index: int | None, date: datetime) -> dict:
    lang = language if language in SIGNS else "en"
    mes = MONTHS[lang][date.month - 1]
    mes_may = mes[:1].upper() + mes[1:]
    if lang == "en":
        fecha = f"{mes} {date.day} {date.year}"          # August 26 2026 (formato del canal EN)
        fecha_larga = f"{WEEKDAYS[lang][date.weekday()]}, {mes} {date.day}, {date.year}"
    elif lang == "pt":
        fecha = f"{date.day} {mes_may} {date.year}"      # 26 Agosto 2026 (formato del canal PT)
        fecha_larga = f"{WEEKDAYS[lang][date.weekday()]}, {date.day} de {mes} de {date.year}"
    else:
        fecha = f"{date.day} De {mes_may} {date.year}"   # 26 De Agosto 2026 (formato del canal ES)
        fecha_larga = f"{WEEKDAYS[lang][date.weekday()].capitalize()} {date.day} De {mes_may} {date.year}"
    v = {
        "fecha": fecha,
        "fecha_may": fecha.upper(),
        "fecha_larga": fecha_larga,
        "dia": str(date.day),
        "mes": mes,
        "mes_may": mes_may,
        "MES": mes.upper(),
        "mes_num": f"{date.month:02d}",
        "anio": str(date.year),
        "dia_semana": WEEKDAYS[lang][date.weekday()],
        "dia_semana_may": WEEKDAYS[lang][date.weekday()].capitalize(),
        "fecha_corta": date.strftime("%d/%m/%Y"),
    }
    if sign_index is not None:
        name = SIGNS[lang][sign_index]
        v.update({
            "signo": name,
            "signo_min": name.lower(),
            "signo_may": name.upper(),
            "emoji": SIGN_EMOJI[sign_index],
            "signo_en": SIGNS["en"][sign_index],          # útil para hashtags en inglés en cualquier idioma
            "signo_en_min": SIGNS["en"][sign_index].lower(),
        })
    return v


YT_DESCRIPTION_MAX = 5000


def trim_description(desc: str, limit: int = YT_DESCRIPTION_MAX - 30) -> tuple[str, bool]:
    """
    Si la descripción supera el límite de YouTube, quita líneas del bloque anterior al cierre
    (normalmente listas de palabras clave), conservando el arranque y las últimas 3 líneas.
    Devuelve (texto, recortada).
    """
    if len(desc) <= limit:
        return desc, False
    lines = desc.split("\n")
    tail_n = 3
    head, tail = lines[:-tail_n], lines[-tail_n:]
    while len("\n".join(head + tail)) > limit and head:
        # quitamos la última línea "de relleno" del cuerpo (no vacía y no encabezado corto)
        idx = max((i for i, l in enumerate(head) if l.strip()), default=None)
        if idx is None:
            break
        del head[idx]
    out = "\n".join(head + tail)
    # colapsar líneas vacías triples que hayan quedado
    while "\n\n\n\n" in out:
        out = out.replace("\n\n\n\n", "\n\n\n")
    return out[:limit], True


def render(text: str, variables: dict) -> str:
    def sub(m):
        return str(variables.get(m.group(1), m.group(0)))
    return re.sub(r"\{(\w+)\}", sub, text or "")


VARIABLES_HELP = [
    ("{signo}", "Libra / Escorpio"), ("{signo_min}", "libra"), ("{signo_may}", "LIBRA"), ("{emoji}", "♎️"),
    ("{signo_en}", "Libra (siempre en inglés)"), ("{signo_en_min}", "libra (inglés)"),
    ("{fecha}", "EN: September 18 2026 · PT: 18 Setembro 2026 · ES: 18 De Septiembre 2026"), ("{fecha_may}", "SEPTEMBER 18 2026"), ("{fecha_larga}", "Viernes 18 De Septiembre 2026"),
    ("{dia}", "18"), ("{mes}", "September / septiembre"), ("{mes_may}", "Septiembre"), ("{MES}", "SEPTEMBER"), ("{mes_num}", "09"), ("{anio}", "2026"),
    ("{dia_semana}", "Friday / viernes"), ("{dia_semana_may}", "Viernes"), ("{fecha_corta}", "18/09/2026"),
    ("{frase}", "La frase del día: se escribe una vez y sale en los 12 signos"),
]


# ───────────────────────── Plantillas de fábrica (se crean solas al arrancar) ─────────────────────────
# Construidas a partir de los documentos reales de cada canal (ES, PT, EN). Si borras una, se vuelve a crear.
DEFAULT_TEMPLATES = [
    {
        "name": "Horóscopo ES — El Horóscopo de Hoy Gratis (doc Aries)",
        "language": "es",
        "date_offset_days": 1,
        "title_template": "{signo_may} {emoji} {frase} Horóscopo De Hoy {fecha} | Mhoni Vidente",
        "description_template": "{signo_may} {emoji} #{signo_min}{emoji}#tarot \n\nBienvenidos A El Horóscopo De Hoy Gratis\n\nHoróscopo De Hoy {signo}, {fecha}. Descubre Tu Futuro Con Mi Horóscopo Diario Especialmente Preparado Para Ti, Mi Querido Signo, En El Amor, Trabajo, Salud, Dinero Y En Tus Compatibilidades Con Otros Signos.\n\nEl Horóscopo De Hoy Gratis #Horoscopodehoy\n\nEn Tu Horóscopo De Hoy, El Destino Tiene Preparadas Grandes Sorpresas Para Ti. Descubre Cómo Se Desarrollará Tu Día Con Nuestras Predicciones Diarias Completamente Gratuitas. Suscríbete Al Canal, Activa La Campanita Y Conoce Tu Destino Gratis Todos Los Días.\n\nHoróscopo De Hoy {signo} {fecha},\nHoróscopo Diario {signo} {fecha}\nHoróscopo {signo} {fecha}\nHoróscopos De Hoy Mhoni Vidente {fecha}\nHoróscopo De Hoy {signo} {fecha}\nHoróscopo De {signo} Hoy. \nHoróscopos Mhoni Vidente Predicciones De Hoy {fecha}\nHoróscopo Diario De Mhoni Vidente Hoy {fecha}\n\nhashtags: \n\n#{signo} #Tarot #{signo}Amor #{signo}hoy #{signo}Dinero #{signo}{dia}{dia} #{signo}{dia}{dia} #{signo}{anio} #{signo}Septiembre{anio} #{signo}Amor #{signo}Almagemela #{signo}Llamagemela #{signo}Soulmates #{signo}Twinflames #{signo}Tarot #{signo}Tarotreading #{signo}Tarotcards #{signo}Tarotamor #{signo}Tarotdelamor #{signo}Tarotdiario #{signo}Tarotastrologico #{signo}Lecturadetarot #{signo}Lecturatarothoy #{signo}Lecturadeeltarothoy #{signo}Lecturahoroscopotarothoy #{signo}Horoscopodehoy #{signo}Horoscopodiario #{signo}EnergiasdelDia #{signo}Predicciones{anio} #{signo}Tarot{anio} #{signo}Tarotenespañol #{signo}Tarotshorts #{signo}TarotdeTallulah #{signo}TallulahTarot #{signo}YouTubeTarot #{signo}Oraculo #{signo}Manifesting #{signo}Destiny #{signo}Astrologia #{signo}Astrology #{signo}Zodiacsigns #{signo}Universe\n\nSuscríbete y descubre tu destino\n\nNo olvides suscribirte a El Horóscopo de Hoy Gratis, activar la campanita y recibir tus predicciones todos los días sin costo alguno. Tu futuro está escrito en las estrellas y nosotros te lo revelamos aquí, en el canal que te conecta con tu destino.\n\ntag:\n\n{signo}, Tarot, {signo} Amor, {signo} hoy, {signo} Dinero, {signo}{dia}{dia}, {signo}{dia}{dia}, {signo}{anio}, {signo}Septiembre{anio}, {signo}Amor, {signo}Almagemela, {signo}Llamagemela, {signo}Soulmates, {signo}Twinflames, {signo}Tarot, {signo}Tarotreading, {signo}Tarotcards, {signo}Tarotamor, {signo}Tarotdelamor, {signo}Tarotdiario, {signo}Tarotastrologico, {signo}Lecturadetarot, {signo}Lecturatarothoy, {signo}Lecturadeeltarothoy, {signo}Lecturahoroscopotarothoy, {signo}Horoscopodehoy, {signo}Horoscopodiario, {signo}EnergiasdelDia, {signo}Predicciones{anio}, {signo}Tarot{anio}, {signo}.\n\nHoróscopo {signo} La Próxima Semana, {signo} Pasado Mañana, {signo} Mañana Tarot, {signo} Mañana Astrostyle, {signo} Horóscopo Mañana En Hindi, {signo} Horóscopo Mensual, Mañana Horóscopo {signo}, ¿Cómo Es {signo} En El Amor?, ¿Qué Signos Son Compatibles Con {signo}?, ¿Qué Cuáles Son Las Fechas Para {signo} ?, ¿En Qué Es Bueno Sexualmente {signo} ?, ¿Por Qué {signo} Es Tan Frío ?, Horóscopo De Amor Único De {signo} Hoy, Horóscopo De Amor De {signo} Hoy Oráculo, Horóscopo De Amor De {signo} Prokerala, Horóscopo De {signo} Mañana Para Estudiantes, {signo} Ama La Vida Hoy Horóscopo De {signo} Mañana Carrera, Horóscopo De La Relación De {signo}, Astrología Del Amor De Hoy Para {signo} Y {signo}, Horóscopo De {signo} La Próxima Semana, {signo} Pasado Mañana, {signo} Mañana Tarot, {signo} Mañana Astrostilo, {signo} Horóscopo Mañana En Hindi, {signo} Horóscopo Mensual, Mañana Horóscopo De {signo}, ¿Cómo Es {signo} En El Amor ?, ¿Qué Signos Son Compatibles Con {signo} ?, ¿Cuáles Son Las Fechas Para {signo} ?, ¿En Qué Es Bueno Sexualmente {signo} ?, ¿Por Qué Es {signo} Tan Frío ?, Horóscopo Del Amor Único De {signo} Hoy, Horóscopo Del Amor De {signo} Hoy O Acle, {signo} Amor Horóscopo Prokerala, {signo} Horóscopo Mañana Para Estudiantes, {signo} Ama La Vida Hoy, {signo} Horóscopo Mañana Carrera, {signo} Relación Horóscopo, Hoy Amor Astrología Para {signo} Y {signo}\nHoróscopos De Hoy Mhoni Vidente, Horóscopos De Mhoni Vidente, Mhoni Vidente Horoscopo De Hoy, Mhoni Vidente Hoy, Mhonividente Horóscopos De {mes_may} De {anio}, Mhonividente Predicciones Noviembre\n\n#{signo} #{mes_may} #Dailytarotreading #{anio}\n\nHoroscopo De Hoy,\n{signo} Hoy,\n Horóscopo {signo},\nHoroscopo De Hoy {signo},\n{signo} {dia_semana_may} {fecha},\n{signo} {fecha},\nHoróscopo {signo} {fecha},\n{signo} Horóscopo De Hoy {fecha},\nHoróscopo {signo} De Hoy {fecha},\nHoróscopo {signo} Para Hoy,\nHoróscopo {signo},\n¡El Horóscopo De Hoy Gratis!",
        "tags_template": "{signo}, Horoscopo De Hoy, {signo} Hoy, Horóscopo {signo}, Horoscopo De Hoy {signo}, {signo} {dia_semana} {fecha}, {signo} {fecha}, Horóscopo {signo} {fecha}, {signo} Horóscopo De Hoy {fecha}, Horóscopo {signo} De Hoy {fecha}, Horóscopo {signo} Para Hoy, {signo} {mes_may} {anio}, Tarot {signo}, Horóscopo Diario {signo}, Tarot, Mhoni Vidente {signo}, Horóscopo, Horoscopo {signo} Hoy, Gabinete De Luz {signo}, {signo} Tarot, Mhoni Vidente Horóscopos {signo}, Moni Vidente Predicción De Hoy, Mhoni Vidente, MHONI"
    },
    {
        "name": "Horóscopo PT (doc Áries)",
        "language": "pt",
        "date_offset_days": 1,
        "title_template": "{frase} {signo} {emoji} {fecha} | Horoscopo do dia de hoje {emoji} Tarot {signo}",
        "description_template": "horóscopo de hoje {signo_min} {fecha}. descubra o seu futuro com o meu horóscopo diário para você, meu querido signo {signo_min} no amor, no trabalho, na saúde, no dinheiro e na compatibilidade com os outros signos. horóscopo diário grátis!\n\nem seu horóscopo hoje, o destino guarda grandes coisas para você. descubra como está seu dia com nossas previsões diárias gratuitas. inscreva-se no canal, ative o símbolo do sino e descubra seu destino gratuitamente todos os dias.\n\nhoróscopo de {signo_min} hoje {fecha},\nhoróscopo diário {signo_min} {fecha},\nhoróscopo {signo_min} {fecha},\nsigno {signo_min} {fecha},\nhoroscopo do dia  {signo_min} {fecha},\n leitura tarô  signo {signo_min} {fecha},\n \n{signo_min}, leitura de tarô diária de {signo_min}, horóscopo diário de {signo_min}, leitura de tarô diária de {signo_min} {anio}, tarô diário de {signo_min}, leitura diária de {signo_min}, leitura diária de {signo_min}, horóscopo diário de {signo_min} hoje, leitura de tarô diária de {signo_min} hoje, leitura de amor diário de {signo_min}, horóscopo financeiro diário de {signo_min}, {signo_min} amor horóscopo, {signo_min} trabalho horóscopo, {signo_min} dinheiro horóscopo, {signo_min} {fecha},  {signo_min} {fecha},  {signo_min} {fecha},\n\n\nhoróscopo de {signo_min} na próxima semana, {signo_min} depois de amanhã, {signo_min} amanhã tarô, {signo_min} amanhã astrostyle, horóscopo de {signo_min} amanhã em hindi, horóscopo de {signo_min} mensal, horóscopo de amanhã para leo, como é {signo_min} no amor?, que signos são compatíveis com {signo_min}? são as datas para touro?, o que {signo_min} é bom sexualmente?, por que {signo_min} é tão frio?, {signo_min} ama a vida hoje, {signo_min} ama o horóscopo hoje, {signo_min} ama o horóscopo prokerala, {signo_min} ama a vida amanhã para estudantes, {signo_min} ama a vida hoje, horóscopo de {signo_min} amanhã, carreira, horóscopo de relacionamento de {signo_min}, astrologia do amor de hoje para {signo_min} e libra, horóscopo de {signo_min} na próxima semana, {signo_min} depois de amanhã, {signo_min} amanhã tarô, {signo_min} amanhã astrostyle, {signo_min} horóscopo amanhã em hindi, {signo_min} horóscopo mensal, horóscopo de amanhã para leo, como é {signo_min} no amor?, quais são os signos compatíveis com {signo_min}?, quais são as datas de touro?, o que {signo_min} é bom sexualmente?, por que {signo_min} é tão frio?, horóscopo do amor solteiro de {signo_min} hoje, {signo_min} ama horóscopo hoje ou acle, {signo_min} ama horóscopo prokerala, {signo_min} horóscopo amanhã para estudantes, {signo_min} ama a vida hoje, {signo_min} horóscopo amanhã carreira, {signo_min} relacionamento horóscopo, astrologia do amor de hoje por {signo_min} e libra\nhoróscopo do amor mensal {signo_min}\nhoróscopo do amor solteiro de {signo_min} {anio}\n{signo_min} ama horóscopo hoje amanhã\nhoróscopo do relacionamento de {signo_min}\n{signo_min} ama o horóscopo de hoje oráculo\n{signo_min} ama a vida hoje\nhoróscopo do amor de {signo_min} {anio}\nhoróscopo diário de {signo_min}\n{signo_min} adora horóscopo prokerala\n{signo_min} {signo_min} adora horóscopo hoje\nhoróscopo do amor de {signo_min} {anio} para solteiros\nhoróscopo do amor de {signo_min}, {mes_may} de {anio}\nhoróscopo do amor de solteiro de {signo_min} hoje\n{signo_min} ama horóscopo hoje oráculo\nhoróscopo do amor diário de {signo_min} ganesha\n{signo_min} ama o horóscopo semanal\n{signo_min} adora horóscopo prokerala\nhoróscopo de {signo_min} amor {anio}\nhoróscopo de {signo_min} hoje\nhoróscopo do relacionamento de {signo_min}\nhoróscopo de {signo_min} amanhã amor\nhoróscopo de {signo_min} amanhã amado\nhoróscopo de {signo_min} amanhã astroyogi\nhoróscopo de {signo_min} amanhã prokerala\nhoróscopo de {signo_min} na próxima semana\n{signo_min} depois de amanhã\ntarô de amanhã de {signo_min}\n{signo_min} amanhã astrostyle\nhoróscopo de {signo_min} amanhã em hindi\nhoróscopo de {signo_min} mensal\n\n#horóscopode{signo_min}, #horóscopodetouro, #horóscopodegêmeos, #horóscopodecâncer, #horóscopodeleão, #horóscopodevirgem, #horóscopolibra, #horóscopoescorpião, #horóscoposagitário, #horóscopocapricórnio, #horóscopoaquário, #horóscopopeixes. #horóscopodehoje, #horóscopodiário, #horóscoposemanal, #horóscopomensal, #horóscopo{anio}, #horóscopo{anio}, #zodíacodehoje, #zodíaco, #horóscopozodíaco, #horóscopode{mes_may}, #horóscopode{mes_may}, #zodíacode{mes_may}, #meuhoróscopo, #meuhoróscopo, #áries, #touro, #gêmeos, #câncer, #leão, #virgem, #libra, #escorpião, #sagitário, #capricórnio, #aquário, #peixes.\n\nhoroscopo do dia de hoje,\nhoróscopo de {signo_min},\nleitura tarô {signo_min} {fecha},\n{signo_min} {fecha},\n{signo_min} {fecha},\nhoróscopo {signo_min} {fecha},\nhoróscopo do dia  {signo_min} {fecha},\nhoróscopo hoje de {signo_min} {fecha},\n {signo_min} {fecha},\nhoróscopo,\n{signo_min} hoje,\nhoróscopo de {signo_min},\ntarô {signo_min},\nhoróscopo diário de {signo_min},\n{signo_min},\nhoróscopo hoje,\nhoróscopo,\nhoróscopo de {signo_min} hoje,\nhoróscopo do dia hoje {signo_min} {fecha},\nhoróscopo do dia hoje,\nsigno de {signo_min} para hoje,\nsigno de {signo_min},\nsigno {signo_min},\nleitura tarô,\n\n{signo_min}, touro, gêmeos, câncer, leão, virgem, libra, escorpião, sagitário, capricórnio, aquário e peixes\n\nhoroscopo do dia,horoscopo do dia de hoje,horoscopo de hoje,horoscopo,horóscopo do dia,signos,no amor,\nna saúde,no dinheiro,na família,números da sorte,frase do dia,signo de {signo_min},signo de touro,signo de gêmeos,\nsigno de câncer,signo de leão,signo de virgem,signo de libra,signo de escorpião,signo dej",
        "tags_template": "horoscopo do dia de hoje, horóscopo de {signo_min}, leitura tarô {signo_min} {fecha}, {signo_min} {fecha}, horóscopo {signo_min} {fecha}, horóscopo do dia {signo_min} {fecha}, horóscopo hoje de {signo_min} {fecha}, horóscopo, {signo_min} hoje, tarô {signo_min}, horóscopo diário de {signo_min}, {signo_min}, horóscopo hoje, horóscopo de {signo_min} hoje, horóscopo do dia hoje {signo_min} {fecha}, horóscopo do dia hoje, signo de {signo_min} para hoje, signo de {signo_min}, signo {signo_min}, leitura tarô"
    },
    {
        "name": "Horóscopo EN — Zodiac Attraction (doc Aries)",
        "language": "en",
        "date_offset_days": 1,
        "title_template": "{signo} {emoji} {frase} horoscope for today {fecha_may} {emoji} #{signo_min} tarot {MES}",
        "description_template": "Discover what destiny has prepared for you every day, zodiac attraction chanel with our reading and daily horoscope totally free!! {signo} today's horoscope - {fecha} - ❤️❤️\nCheck your {signo_min} horoscope, check your {signo_min} daily horoscope today - {fecha} - {emoji}\n\nCheck your prediction zodiac attraction {signo_min} {fecha} ❤️ {signo_min} horoscope, in your horoscope channel for today find your horoscope for today {fecha} for free, discover what destiny has in store for you in love, health and money.\n\n{signo} daily horoscope {fecha},\n\n{signo} horoscope today {fecha},\nDaily horoscope {signo_min} {fecha},\nHoroscope {signo_min} {fecha},\n\n{signo},{signo_min} daily tarot reading,{signo_min} daily horoscope,{signo_min} daily tarot reading {anio},{signo_min} daily tarot,{signo_min} daily reading,{signo_min} daily reading,{signo_min} daily horoscope today,{signo_min} daily tarot reading today,{signo_min} daily love reading,{signo_min} daily finance horoscope,zodiac attraction, {signo_min} love horoscope,{signo_min} work horoscope,{signo_min} money horoscope,{signo_min} {fecha}, ,{signo_min}  {fecha}, {signo_min} {fecha},\n\n\n{signo} horoscope next week,{signo_min} day after tomorrow,{signo_min} tomorrow tarot,{signo_min} tomorrow astrostyle,{signo_min} horoscope tomorrow in hindi,{signo_min} horoscope monthly,tomorrow horoscope for leo,what are {signo_min} like in love?,what signs are compatible with {signo_min}?,what are the dates for taurus?,what are {signo_min} good at sexually?,why are {signo_min} so cold?,{signo_min} single love horoscope today,{signo_min} love horoscope today oracle,{signo_min} love horoscope prokerala,{signo_min} horoscope tomorrow for students,{signo_min} love life today,{signo_min} horoscope tomorrow career,{signo_min} relationship horoscope,today's love astrology for {signo_min} and libra,{signo_min} horoscope next week,{signo_min} day after tomorrow,{signo_min} tomorrow tarot,{signo_min} tomorrow astrostyle,{signo_min} horoscope tomorrow in hindi,{signo_min} horoscope monthly,tomorrow horoscope for leo,what are {signo_min} like in love?,what signs are compatible with {signo_min}?,what are the dates for taurus?,what are {signo_min} good at sexually?,why are {signo_min} so cold?,{signo_min} single love horoscope today,{signo_min} love horoscope today oracle,{signo_min} love horoscope prokerala,{signo_min} horoscope tomorrow for students,{signo_min} love life today,{signo_min} horoscope tomorrow career,zodiac attraction,{signo_min} relationship horoscope,today's love astrology for {signo_min} and libra monthly love horoscope {signo_min}\n{signo} single love horoscope {anio}\n{signo} love horoscope today tomorrow\n{signo} relationship horoscope\n{signo_min} love horoscope today oracle\n{signo} love life today\n{signo} love horoscope {anio}\n{signo} daily horoscope\n{signo} love horoscope prokerala\n{signo} {signo_min} love horoscope today\n{signo} single love horoscope today\n{signo} love horoscope weekly\n{signo} love horoscope prokerala\n{signo} horoscope love\n{signo} horoscope today\nZodiac attraction\n\n#{signo_en_min}horoscope , #taurushoroscope , #geminihoroscope , #cancerhoroscope , #leohoroscope , #virgohoroscope , #librahoroscope , #scorpiohoroscope , #sagittariushoroscope , #zodiacattraction  #capricornhoroscope , #aquariushoroscope  #pisceshoroscope  #tarot  #horoscopefortoday  #freedailyhoroscope  #tarotreading  #horoscopetoday  #horoscopefortoday   #horoscopefortoday   #dailyhoroscope  #dailytarotreading{anio}  #horoscopetoday  #dailytarotreadingtoday  #love  #lucky  #{anio}  #freetarotreading #horoscopetarot  #horoscopes #horoscope \n\n\n subscribe and discover your future.\n\nHoroscope for today,\n{signo},\n{signo} daily horoscope today,\n{signo} daily tarot reading today,\n{signo}  {fecha},\n{signo} {mes} {dia} ,\n{signo} horoscope {fecha},\n{signo} today's horoscope {fecha},\nToday's horoscope {signo_min} {fecha},\nDaily horoscope,\n{signo} horoscope for today,\nFree horoscope,\nHoroscope,\nToday's horoscope,\nVanessa somuayina,\n{signo} today,\nLucky,\n{anio},\n{signo} horoscope,\nHoroscope for today,\n{signo} tarot,\n{signo} tarot today,\n{signo} tarot reading,\n{signo} horoscope {fecha},\n{signo} daily horoscope,\nTarot,\nLove,\n{signo} daily horoscope,\nHoroscope today {signo_min},\nHoroscope for today {signo_min},\nHoroscope today,\nHoroscope {signo_min} money\n{signo} love horoscope\nHoroscope {signo_min} tarot\n{signo} horoscope luck\n\n{signo} love tarot,tarot {signo_min} {anio},{signo_min} money,{signo_min} {anio} career,{signo_min} tarot reading,tarot reading,{signo_min} tarot,{signo_min} {anio} tarot reading,{signo_min} {anio},tarot,{signo_min} love tarot,{signo_min} {anio} tarot,{signo_min} {anio} predictions,{signo_min} tarot reading {anio},{signo_min} {anio} tarot reading,{signo_min} {mes} {anio},{signo_min},{signo_min} {anio} love,{signo_min} {anio} horoscope,best tarot reading,tagalog tarot reading {anio},{signo_min} tarot reading today,{signo_min} tarot {anio},tagalog tarot reading,{signo_min} {anio} yearly love reading,{signo_min} love tarot reading {anio},{signo_min} tarot reading,tarot reading,{signo_min} tarot,{signo_min} {anio} tarot reading,{signo_min} {anio},tarot,{signo_min} love tarot,{signo_min} {anio} tarot,{signo_min} {anio} predictions,{signo_min} tarot reading {anio},{signo_min} {anio} tarot reading,{signo_min} {mes} {anio},{signo_min},{signo_min} {anio} love,{signo_min} {anio} horoscope,best tarot reading,tagalog tarot reading {anio},{signo_min} tarot reading today,\n\n#aries #taurus #gemini #cancer #leo #virgo #libra #scorpio #sagittarius #capricorn #aquarius #pisces  \n\nSuscribe for more daily",
        "tags_template": "horoscope for today, {signo_min}, {signo_min} daily horoscope today, {signo_min} tarot today, {signo_min} {fecha}, {signo_min} {mes} {dia}, {signo_min} horoscope {fecha}, {signo_min} today's horoscope {fecha}, today's horoscope {signo_min} {fecha}, daily horoscope, {signo_min} horoscope for today, horoscope, today's horoscope, vanessa somuayina, {signo_min} today, {anio}, {signo_min} horoscope, {signo_min} tarot, {signo_min} tarot reading, {signo_min} daily horoscope, tarot, horoscope today {signo_min}, horoscope for today {signo_min}"
    }
]


@app.on_event("startup")
def seed_default_templates() -> None:
    db = SessionLocal()
    try:
        existing = {t.language for t in db.query(RotatingTemplate).all()}
        created = 0
        for d in DEFAULT_TEMPLATES:
            if d["language"] in existing:
                continue
            db.add(RotatingTemplate(
                name=d["name"], language=d["language"], date_offset_days=d["date_offset_days"],
                title_template=d["title_template"], description_template=d["description_template"],
                tags_template=d["tags_template"],
            ))
            created += 1
        db.commit()
        if created:
            print(f"[startup] Plantillas de fábrica creadas: {created}")
    finally:
        db.close()


# ───────────────────────── CRUD ─────────────────────────
def _out(t: RotatingTemplate) -> dict:
    all_text = (t.title_template or "") + (t.description_template or "") + (t.tags_template or "")
    return {
        "id": t.id, "own_channel_id": t.own_channel_id, "channel_title": t.own_channel.title if t.own_channel else None,
        "name": t.name, "language": t.language, "date_offset_days": t.date_offset_days,
        "title_template": t.title_template, "description_template": t.description_template,
        "tags_template": t.tags_template,
        "uses_sign": "{signo" in all_text or "{emoji}" in all_text,
        "uses_phrase": "{frase}" in all_text,
        "created_at": t.created_at,
    }


class TemplateBody(BaseModel):
    own_channel_id: int | None = None
    name: str
    language: str = "en"
    date_offset_days: int = 1
    title_template: str = ""
    description_template: str = ""
    tags_template: str = ""   # etiquetas separadas por coma, admite variables


@router.get("")
def list_templates(role: str = Depends(require_editor), db: Session = Depends(get_db)):
    rows = db.query(RotatingTemplate).order_by(RotatingTemplate.name).all()
    return [_out(t) for t in rows]


@router.get("/meta")
def meta(role: str = Depends(require_editor)):
    return {"signs": SIGNS, "emojis": SIGN_EMOJI, "variables": VARIABLES_HELP}


@router.post("")
def create_template(body: TemplateBody, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    if body.own_channel_id and not db.get(OwnChannel, body.own_channel_id):
        raise HTTPException(404, "Canal no encontrado")
    t = RotatingTemplate(**body.model_dump())
    db.add(t)
    db.commit()
    return _out(t)


@router.put("/{pk}")
def update_template(pk: int, body: TemplateBody, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    t = db.get(RotatingTemplate, pk)
    if not t:
        raise HTTPException(404, "Plantilla no encontrada")
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    db.commit()
    return _out(t)


@router.delete("/{pk}")
def delete_template(pk: int, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    t = db.get(RotatingTemplate, pk)
    if not t:
        raise HTTPException(404, "Plantilla no encontrada")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ───────────────────────── Frase del día ─────────────────────────
def _get_phrase(db: Session, pk: int) -> dict:
    row = db.get(Setting, f"tpl_phrase_{pk}")
    return row.value if row else {"text": "", "date": None}


class PhraseBody(BaseModel):
    text: str


@router.get("/{pk}/phrase")
def get_phrase(pk: int, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    return _get_phrase(db, pk)


@router.put("/{pk}/phrase")
def set_phrase(pk: int, body: PhraseBody, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    """La frase del día: se escribe una vez y la ven todos (extensión incluida)."""
    if not db.get(RotatingTemplate, pk):
        raise HTTPException(404, "Plantilla no encontrada")
    row = db.get(Setting, f"tpl_phrase_{pk}") or Setting(key=f"tpl_phrase_{pk}", value={})
    row.value = {"text": body.text.strip(), "date": local_today().strftime("%Y-%m-%d")}
    db.add(row)
    db.commit()
    return row.value


# ───────────────────────── Render ─────────────────────────
class RenderBody(BaseModel):
    sign_index: int | None = None     # 0 = Aries … 11 = Piscis
    date: str | None = None           # YYYY-MM-DD; si no, hoy + date_offset_days


def _render_template(db: Session, t: RotatingTemplate, body: RenderBody) -> dict:
    if body.date:
        date = datetime.strptime(body.date, "%Y-%m-%d")
    else:
        date = local_today() + timedelta(days=t.date_offset_days or 0)
    v = build_variables(t.language, body.sign_index, date)
    phrase = _get_phrase(db, t.id)
    v["frase"] = phrase.get("text", "")
    tags = [x.strip() for x in render(t.tags_template, v).split(",") if x.strip()]
    description, trimmed = trim_description(render(t.description_template, v))
    return {
        "title": " ".join(render(t.title_template, v).split()),
        "description": description,
        "description_chars": len(description),
        "description_trimmed": trimmed,
        "tags": tags,
        "date_used": date.strftime("%Y-%m-%d"),
        "phrase": phrase,
        "phrase_missing": "{frase}" in (t.title_template or "") and not phrase.get("text"),
        "variables": v,
    }


@router.post("/{pk}/render")
def render_template(pk: int, body: RenderBody, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    t = db.get(RotatingTemplate, pk)
    if not t:
        raise HTTPException(404, "Plantilla no encontrada")
    return _render_template(db, t, body)


class ApplyBody(RenderBody):
    video_id: str
    write_title: bool = False


@router.post("/{pk}/apply")
def apply_template(pk: int, body: ApplyBody, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    """Escribe la plantilla ya rellenada en un video existente (videos.update, 51 unidades)."""
    t = db.get(RotatingTemplate, pk)
    if not t:
        raise HTTPException(404, "Plantilla no encontrada")
    if not t.own_channel:
        raise HTTPException(400, "La plantilla no tiene canal asignado")
    token = yt.get_valid_token(t.own_channel, db)
    r = _render_template(db, t, body)
    current = yt.get_video_snippet(db, token, body.video_id)
    title = r["title"] if (body.write_title and r["title"].strip()) else current.get("title", "")
    yt.update_video_metadata(db, token, body.video_id, title, r["description"], r["tags"] or current.get("tags", []))
    t.last_run_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, **r, "quota": yt.quota_today(db)}

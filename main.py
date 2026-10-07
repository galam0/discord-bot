import discord
import os
import re
import asyncio
import aiohttp
import traceback
from collections import OrderedDict
from urllib.parse import quote
from bs4 import BeautifulSoup
from flask import Flask
from threading import Thread

# ==========================================
# 1. 24시간 유지를 위한 웹 서버 설정 (Flask)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "포켓몬 봇이 24시간 온라인 상태입니다!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run_server, daemon=True).start()

# ==========================================
# 2. 공용 설정 / 데이터 사전
# ==========================================
POKEAPI = "https://pokeapi.co/api/v2"
WIKI_BASE = "https://pixelmonmod.com"

# setup_hook에서 생성 (이벤트 루프가 뜬 뒤 만들어야 안전)
HTTP_SESSION: aiohttp.ClientSession = None
HTTP_SEM: asyncio.Semaphore = None

POKEMON_KO_TO_ID = {}     # '피카츄' -> '25'
SPECIES_EN_TO_KO = {}     # 'pikachu' -> '피카츄'
KOREAN_CACHE_READY = False

TYPE_KO = {
    'normal': '⚪ 노말', 'fire': '🔥 불꽃', 'water': '💧 물',
    'grass': '🌿 풀', 'electric': '⚡ 전기', 'ice': '❄️ 얼음',
    'fighting': '🥊 격투', 'poison': '☠️ 독', 'ground': '⏳ 땅',
    'flying': '🕊️ 비행', 'psychic': '🔮 에스퍼', 'bug': '🐛 벌레',
    'rock': '🪨 바위', 'ghost': '👻 고스트', 'dragon': '🐉 드래곤',
    'steel': '⚙️ 강철', 'fairy': '✨ 페어리', 'dark': '🌙 악'
}

# PokeAPI 실제 알 그룹 키 기준
EGG_GROUP_KO = {
    'monster': '괴수', 'water1': '수중 1', 'water2': '수중 2', 'water3': '수중 3',
    'bug': '벌레', 'flying': '비행', 'ground': '육상', 'fairy': '요정',
    'plant': '식물', 'humanshape': '인간형', 'mineral': '광물',
    'indeterminate': '부정형', 'dragon': '드래곤', 'ditto': '메타몽',
    'no-eggs': '미발견',
}

# --- 폼 체인지 번역 및 이모지 사전 ---
FORM_TRANSLATIONS = {
    'mega-x': ('메가진화 X', '✨', discord.ButtonStyle.success),
    'mega-y': ('메가진화 Y', '✨', discord.ButtonStyle.success),
    'mega': ('메가진화', '✨', discord.ButtonStyle.success),
    'gmax': ('거다이맥스', '💥', discord.ButtonStyle.danger),
    'alola': ('알로라 폼', '🏝️', discord.ButtonStyle.success),
    'galar': ('가라르 폼', '🛡️', discord.ButtonStyle.success),
    'hisui': ('히스이 폼', '📜', discord.ButtonStyle.success),
    'paldea': ('팔데아 폼', '🥊', discord.ButtonStyle.success),
    'primal': ('원시회귀', '🌋', discord.ButtonStyle.danger),
    'origin': ('오리진 폼', '🔮', discord.ButtonStyle.primary),
    'therian': ('영수 폼', '🦅', discord.ButtonStyle.success),
    'black': ('블랙 폼', '⚡', discord.ButtonStyle.primary),
    'white': ('화이트 폼', '🔥', discord.ButtonStyle.danger),
    'dusk': ('황혼의 갈기', '🦁', discord.ButtonStyle.primary),
    'dawn': ('새벽의 날개', '🦇', discord.ButtonStyle.primary),
    'ultra': ('울트라 폼', '🌟', discord.ButtonStyle.danger),
    '10': ('10% 폼', '🐕', discord.ButtonStyle.success),
    'complete': ('퍼펙트 폼', '🤖', discord.ButtonStyle.primary),
    'school': ('군집의 모습', '🐟', discord.ButtonStyle.primary),
    'attack': ('공격 폼', '⚔️', discord.ButtonStyle.danger),
    'defense': ('방어 폼', '🛡️', discord.ButtonStyle.primary),
    'speed': ('스피드 폼', '⚡', discord.ButtonStyle.success),
    'heat': ('히트 폼', '🔥', discord.ButtonStyle.danger),
    'wash': ('워시 폼', '💧', discord.ButtonStyle.primary),
    'frost': ('프로스트 폼', '❄️', discord.ButtonStyle.primary),
    'fan': ('스핀 폼', '🌪️', discord.ButtonStyle.success),
    'mow': ('커트 폼', '🌿', discord.ButtonStyle.success),
    'blade': ('블레이드 폼', '⚔️', discord.ButtonStyle.danger),
    'unbound': ('굴레를 벗어난 폼', '🌀', discord.ButtonStyle.danger),
    'sky': ('스카이 폼', '🌱', discord.ButtonStyle.success),
    'hero': ('마이티 폼', '🦸', discord.ButtonStyle.primary),
    'normal': ('노말 폼', '🔄', discord.ButtonStyle.secondary),
}
# 이름 앞에 붙이는 폼 (예: "메가 X 리자몽", "알로라 나인테일")
PREFIX_FORMS = {'mega-x', 'mega-y', 'mega', 'alola', 'galar', 'hisui',
                'paldea', 'primal', 'black', 'white', 'ultra'}

# ==========================================
# 3. HTTP 헬퍼 (비동기 + 타임아웃 + 동시요청 제한 + 캐시)
# ==========================================
JSON_CACHE = OrderedDict()
JSON_CACHE_MAX = 600

async def fetch_json(url, use_cache=True):
    if use_cache and url in JSON_CACHE:
        JSON_CACHE.move_to_end(url)
        return JSON_CACHE[url]
    async with HTTP_SEM:
        try:
            async with HTTP_SESSION.get(url) as r:
                if r.status != 200:
                    return None
                data = await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None
    if use_cache:
        JSON_CACHE[url] = data
        if len(JSON_CACHE) > JSON_CACHE_MAX:
            JSON_CACHE.popitem(last=False)
    return data

async def fetch_text(url):
    """(status, text) 반환. 네트워크 오류면 status=0"""
    async with HTTP_SEM:
        try:
            async with HTTP_SESSION.get(url) as r:
                return r.status, (await r.text() if r.status == 200 else "")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return 0, ""

# ==========================================
# 4. 한국어 사전 구축 (봇 시작 시 1회)
# ==========================================
async def fetch_species_name(url):
    data = await fetch_json(url, use_cache=False)
    if not data:
        return False
    for n in data.get('names', []):
        if n['language']['name'] == 'ko':
            ko = n['name']
            POKEMON_KO_TO_ID[ko.replace(' ', '').lower()] = str(data['id'])
            SPECIES_EN_TO_KO[data['name']] = ko
            break
    return True

async def build_korean_cache():
    global KOREAN_CACHE_READY
    print('🔄 포켓몬 한국어 데이터베이스 수집 중...')
    listing = await fetch_json(f'{POKEAPI}/pokemon-species?limit=10000', use_cache=False)
    if not listing:
        print('❌ 종 목록을 불러오지 못했습니다.')
        return
    urls = [item['url'] for item in listing['results']]
    results = await asyncio.gather(*(fetch_species_name(u) for u in urls))
    failed = [u for u, ok in zip(urls, results) if not ok]
    if failed:  # 실패한 것만 한 번 재시도
        await asyncio.gather(*(fetch_species_name(u) for u in failed))
    KOREAN_CACHE_READY = True
    print(f"✅ 데이터베이스 수집 완료! ({len(POKEMON_KO_TO_ID)}종)")

def ko_name(species_en):
    return SPECIES_EN_TO_KO.get(species_en, species_en.replace('-', ' ').title())

# ==========================================
# 5. 특성 / 알 그룹 / 부화 / 포획률
# ==========================================
ABILITY_KO_CACHE = {}

async def get_ability_korean_name(ability):
    key = ability['name']
    if key in ABILITY_KO_CACHE:
        return ABILITY_KO_CACHE[key]
    data = await fetch_json(ability['url'])
    if data:
        for entry in data.get('names', []):
            if entry.get('language', {}).get('name') == 'ko' and entry.get('name'):
                ABILITY_KO_CACHE[key] = entry['name']
                return entry['name']
        fallback = key.replace('-', ' ').title()
        ABILITY_KO_CACHE[key] = fallback
        return fallback
    return key.replace('-', ' ').title()  # 네트워크 실패는 캐시하지 않음

async def get_pokemon_extra_info(data, species_data):
    entries = data.get('abilities', [])
    names = await asyncio.gather(*(get_ability_korean_name(e['ability']) for e in entries))

    normal_abilities, hidden_abilities = [], []
    for entry, name in zip(entries, names):
        target = hidden_abilities if entry.get('is_hidden') else normal_abilities
        if name not in target:
            target.append(name)

    ability_text = ", ".join(normal_abilities) or "정보 없음"
    hidden_text = ", ".join(hidden_abilities) or "없음"

    egg_groups = []
    for group in species_data.get('egg_groups', []):
        g = group.get('name', '')
        k = EGG_GROUP_KO.get(g, g.replace('-', ' ').title())
        if k and k not in egg_groups:
            egg_groups.append(k)
    egg_group_text = ", ".join(egg_groups) if egg_groups else "미발견"

    hatch_counter = species_data.get('hatch_counter')
    hatch_text = f"{255 * (hatch_counter + 1):,}걸음" if hatch_counter is not None else "정보 없음"
    capture_rate = species_data.get('capture_rate')
    catch_text = str(capture_rate) if capture_rate is not None else "정보 없음"

    return ability_text, hidden_text, egg_group_text, hatch_text, catch_text

# ==========================================
# 6. 픽셀몬 위키 스폰 정보 (바닐라 바이옴만)
# ==========================================
PIXELMON_SPAWN_CACHE = {}
BIOME_LINK_CACHE = {}
LEGENDARY_SOUP = None
TIME_ORDER = ["Dawn", "Morning", "Day", "Midday", "Afternoon", "Dusk", "Night", "Midnight", "Any"]
VANILLA_MOD_IDS = {'', '—', '-', 'minecraft', 'vanilla'}

WIKI_TITLE_EXCEPTIONS = {
    'mr-mime': 'Mr._Mime', 'mime-jr': 'Mime_Jr.', 'mr-rime': 'Mr._Rime',
    'ho-oh': 'Ho-Oh', 'porygon-z': 'Porygon-Z', 'type-null': 'Type:_Null',
    'jangmo-o': 'Jangmo-o', 'hakamo-o': 'Hakamo-o', 'kommo-o': 'Kommo-o',
    'farfetchd': "Farfetch'd", 'sirfetchd': "Sirfetch'd",
    'nidoran-f': 'Nidoran♀', 'nidoran-m': 'Nidoran♂', 'flabebe': 'Flabébé',
    'wo-chien': 'Wo-Chien', 'chien-pao': 'Chien-Pao', 'ting-lu': 'Ting-Lu', 'chi-yu': 'Chi-Yu',
}

def wiki_title(species_name):
    if species_name in WIKI_TITLE_EXCEPTIONS:
        return WIKI_TITLE_EXCEPTIONS[species_name]
    return '_'.join(p.capitalize() for p in species_name.split('-'))

def normalize_wiki_name(name):
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()

def clean_biome_text(text):
    if not text:
        return []
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*\((Forge|Mod)\)", "", text, flags=re.IGNORECASE).strip()
    invalid_keywords = ["voice", "endure", "ratio", "gender", "level", "wild", "tm", "tutor", "egg"]
    if not text or any(bad in text.lower() for bad in invalid_keywords):
        return []
    return [text]

def extract_times(text):
    return [tk for tk in TIME_ORDER if re.search(rf"\b{re.escape(tk)}\b", text or "", re.IGNORECASE)]

async def expand_biome_link(href, label):
    """
    링크가 'Arid' 같은 바이옴 카테고리 페이지면 그 안의 '바닐라' 바이옴 목록으로 펼치고,
    일반 바이옴 페이지면 이름 그대로 반환.
    """
    if not href or 'redlink=1' in href or not href.startswith('/'):
        return clean_biome_text(label)
    if href in BIOME_LINK_CACHE:
        return BIOME_LINK_CACHE[href]

    status, html = await fetch_text(WIKI_BASE + href)
    if status == 0:
        return clean_biome_text(label)  # 네트워크 실패는 캐시하지 않음
    if status != 200:
        result = clean_biome_text(label)
        BIOME_LINK_CACHE[href] = result
        return result

    soup = BeautifulSoup(html, "html.parser")
    content = soup.find(id="mw-content-text") or soup

    vanilla_names, found_vanilla_table = [], False
    for h in content.find_all(["h2", "h3"]):
        if "vanilla" in h.get_text(" ", strip=True).lower():
            table = h.find_next("table")
            if table and "biome id" in table.get_text(" ", strip=True).lower():
                found_vanilla_table = True
                for row in table.find_all("tr"):
                    td = row.find("td")
                    if td:
                        vanilla_names.extend(clean_biome_text(td.get_text(" ", strip=True)))
            break

    if found_vanilla_table:
        result = vanilla_names
    elif re.search(r"is a biome category", content.get_text(" ", strip=True), re.IGNORECASE):
        result = []  # 카테고리지만 바닐라 바이옴이 없음 (모드 전용)
    else:
        result = clean_biome_text(label)  # 일반 바이옴 페이지

    BIOME_LINK_CACHE[href] = result
    return result

async def expand_biome_cell(cell):
    links = [a for a in cell.find_all("a") if a.get("href")]
    if not links:
        return clean_biome_text(cell.get_text(" ", strip=True))
    results = await asyncio.gather(*(expand_biome_link(a["href"], a.get_text(" ", strip=True)) for a in links))
    return [name for sub in results for name in sub]

async def parse_normal_spawn_table(soup):
    """(biomes, times, had_rows) 반환. had_rows: 스폰 표에 행이 하나라도 있었는지"""
    biomes, times, had_rows = set(), set(), False
    heading = next((t for t in soup.find_all(["h2", "h3"])
                    if "spawn biomes" in t.get_text(" ", strip=True).lower()), None)
    if not heading:
        return biomes, times, had_rows
    table = heading.find_next("table")
    if not table:
        return biomes, times, had_rows

    col = {'biome': 0, 'modid': 1, 'time': 2}
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        texts = [c.get_text(" ", strip=True) for c in cells]
        lowered = [t.lower() for t in texts]

        if "biome" in lowered:  # 헤더 행 → 열 위치 파악
            for name in ('biome', 'modid', 'time'):
                if name in lowered:
                    col[name] = lowered.index(name)
            continue
        if len(cells) <= max(col.values()):
            continue

        had_rows = True
        if lowered[col['modid']] not in VANILLA_MOD_IDS:
            continue  # 모드 바이옴 행은 건너뜀

        biomes.update(await expand_biome_cell(cells[col['biome']]))
        times.update(extract_times(texts[col['time']]))
    return biomes, times, had_rows

async def parse_legendary_spawn_table(title):
    global LEGENDARY_SOUP
    if LEGENDARY_SOUP is None:
        status, html = await fetch_text(f"{WIKI_BASE}/wiki/Legendary_Pokemon")
        if status != 200:
            return set(), set()
        LEGENDARY_SOUP = BeautifulSoup(html, "html.parser")

    target = normalize_wiki_name(title)
    for table in LEGENDARY_SOUP.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) < 4:
                continue
            if normalize_wiki_name(cells[0].get_text(" ", strip=True)) == target:
                biomes = await expand_biome_cell(cells[1])
                times = extract_times(cells[3].get_text(" ", strip=True))
                return set(biomes), set(times)
    return set(), set()

async def get_pixelmon_spawn_info(species_name):
    if species_name in PIXELMON_SPAWN_CACHE:
        return PIXELMON_SPAWN_CACHE[species_name]

    title = wiki_title(species_name)
    encoded = quote(title, safe="_:'.-")
    status, html = await fetch_text(f"{WIKI_BASE}/wiki/{encoded}")
    if status == 0:
        return ("정보 없음 (위키 연결 실패)", "Any")

    biomes, times, had_rows = set(), set(), False
    if status == 200:
        biomes, times, had_rows = await parse_normal_spawn_table(BeautifulSoup(html, "html.parser"))

    if not had_rows:
        l_biomes, l_times = await parse_legendary_spawn_table(title.replace('_', ' '))
        biomes |= l_biomes
        times |= l_times

    if biomes:
        biome_text = ", ".join(sorted(biomes, key=str.lower))
        if len(biome_text) > 1000:
            biome_text = biome_text[:997] + "..."
    elif had_rows:
        biome_text = "바닐라 바이옴 없음 (모드 바이옴 전용)"
    else:
        biome_text = "정보 없음"

    sorted_times = [t for t in TIME_ORDER if t in times]
    time_text = ", ".join(sorted_times) if sorted_times else "Any"

    PIXELMON_SPAWN_CACHE[species_name] = (biome_text, time_text)
    return biome_text, time_text

# ==========================================
# 7. 포켓몬 조회 / 폼 처리
# ==========================================
async def fetch_pokemon(query):
    q = query.strip().lower()

    if re.search(r'[가-힣]', q):
        sid = POKEMON_KO_TO_ID.get(q.replace(' ', ''))
        return await fetch_json(f"{POKEAPI}/pokemon/{sid}") if sid else None

    q = q.replace(' ', '-')
    data = await fetch_json(f"{POKEAPI}/pokemon/{q}")
    if data:
        return data
    # 'deoxys'처럼 종 이름으로는 /pokemon 조회가 안 되는 경우 → 기본 폼으로
    species = await fetch_json(f"{POKEAPI}/pokemon-species/{q}")
    if species:
        return await fetch_json(f"{POKEAPI}/pokemon/{species['id']}")
    return None

def get_form_suffix(pokemon_name, species_name):
    prefix = species_name + '-'
    return pokemon_name[len(prefix):] if pokemon_name.startswith(prefix) else ''

def match_form(suffix):
    """접미사 기준으로만 비교 (meganium, dusknoir 같은 오탐 방지)"""
    if not suffix:
        return None
    if suffix in FORM_TRANSLATIONS:
        return suffix
    for key in sorted(FORM_TRANSLATIONS, key=len, reverse=True):
        if suffix.startswith(key + '-'):
            return key
    return None

def get_default_variety(species_data):
    for v in species_data.get('varieties', []):
        if v.get('is_default'):
            return v['pokemon']['name']
    return species_data['name']

def find_evolutions(node, target, parent=None):
    """진화 트리 전체를 재귀 탐색 → (이전 진화, [다음 진화들]) 또는 None"""
    name = node['species']['name']
    if name == target:
        return parent, [c['species']['name'] for c in node['evolves_to']]
    for child in node['evolves_to']:
        found = find_evolutions(child, target, name)
        if found:
            return found
    return None

# ==========================================
# 8. Embed 생성기
# ==========================================
async def build_embed(data, species_data):
    display_name = ko_name(species_data['name'])
    for entry in species_data.get('names', []):
        if entry['language']['name'] == 'ko':
            display_name = entry['name']
            break

    if not data.get('is_default', True):
        suffix = get_form_suffix(data['name'], species_data['name'])
        key = match_form(suffix)
        if key:
            k_name = FORM_TRANSLATIONS[key][0]
            if key in PREFIX_FORMS:
                display_name = f"{k_name.replace(' 폼', '').replace('진화', '')} {display_name}"
            else:
                display_name = f"{display_name} ({k_name})"
        elif suffix:
            display_name = f"{display_name} ({suffix.replace('-', ' ').title()} 폼)"

    sprites = data.get('sprites', {})
    image_url = (sprites.get('other', {}).get('official-artwork', {}).get('front_default')
                 or sprites.get('front_default'))

    types_str = ", ".join(TYPE_KO.get(t['type']['name'], t['type']['name'].capitalize()) for t in data['types'])

    stats = {s['stat']['name']: s['base_stat'] for s in data['stats']}
    total = sum(stats.values())
    border = "+" + "-" * 38 + "+"
    stats_table = (
        f"```text\n{border}\n"
        "|  HP  Atk  Def  SpA  SpD  Spe | Total |\n"
        f"{border}\n"
        f"| {stats.get('hp',0):>3}  {stats.get('attack',0):>3}  {stats.get('defense',0):>3}  "
        f"{stats.get('special-attack',0):>3}  {stats.get('special-defense',0):>3}  {stats.get('speed',0):>3} |  {total:>3}  |\n"
        f"{border}```"
    )

    (biome_info, time_info), (ability_text, hidden_text, egg_group_text, hatch_text, catch_text) = await asyncio.gather(
        get_pixelmon_spawn_info(species_data['name']),
        get_pokemon_extra_info(data, species_data),
    )

    rarity_badge, rarity_color = "", 0x3498db
    if species_data.get("is_legendary"):
        rarity_badge, rarity_color = "🌟 전설 ", 0xF1C40F
    elif species_data.get("is_mythical"):
        rarity_badge, rarity_color = "✨ 환상 ", 0x9B59B6

    embed = discord.Embed(title=f"{rarity_badge}포켓몬 정보: {display_name}", color=rarity_color)
    if image_url:
        embed.set_thumbnail(url=image_url)
    embed.add_field(name="📖 도감 번호", value=f"No.{species_data['id']}", inline=False)
    embed.add_field(name="⚔️ 타입", value=types_str, inline=True)
    embed.add_field(name="🛡️ 특성", value=ability_text, inline=True)
    embed.add_field(name="🔒 숨겨진 특성", value=hidden_text, inline=True)
    embed.add_field(name="🥚 알 그룹", value=egg_group_text, inline=True)
    embed.add_field(name="🎯 포획률", value=catch_text, inline=True)
    embed.add_field(name="🐣 부화 걸음 수", value=hatch_text, inline=True)
    embed.add_field(name="🍄 출몰 바이옴", value=biome_info, inline=True)
    embed.add_field(name="⏳ 출몰 시간", value=time_info, inline=True)
    embed.add_field(name="🌸 종족치", value=stats_table, inline=False)
    return embed

async def build_pokemon_message(query):
    """(embed, view) 또는 None"""
    data = await fetch_pokemon(query)
    if not data:
        return None
    species_data = await fetch_json(data['species']['url'])
    if not species_data:
        return None

    async def get_chain():
        url = (species_data.get('evolution_chain') or {}).get('url')
        if not url:
            return None
        chain_json = await fetch_json(url)
        return chain_json['chain'] if chain_json else None

    embed, chain = await asyncio.gather(build_embed(data, species_data), get_chain())
    view = PokemonView(species_data, data, chain)
    return embed, view

# ==========================================
# 9. 진화 및 폼 체인지 버튼 뷰
# ==========================================
class PokemonView(discord.ui.View):
    MAX_BUTTONS = 25

    def __init__(self, species_data, pokemon_data, chain):
        super().__init__(timeout=180)
        self.message = None
        self._targets = set()

        current = pokemon_data['name']
        default_name = get_default_variety(species_data)
        self._targets.add(current)

        # 1. 원래 형태 (같은 종의 기본 폼)
        if current != default_name:
            self._add(default_name, "🔄 원래 형태", discord.ButtonStyle.secondary)

        # 2. 진화 버튼
        if chain:
            found = find_evolutions(chain, species_data['name'])
            if found:
                prev_evo, next_evos = found
                if prev_evo:
                    self._add(prev_evo, f"⬅️ {ko_name(prev_evo)}", discord.ButtonStyle.secondary)
                for n in next_evos:
                    self._add(n, f"진화: {ko_name(n)}", discord.ButtonStyle.primary)

        # 3. 폼 체인지 버튼
        for var in species_data.get('varieties', []):
            name = var['pokemon']['name']
            if name in (current, default_name):
                continue
            suffix = get_form_suffix(name, species_data['name'])
            key = match_form(suffix)
            if key:
                label, emoji, style = FORM_TRANSLATIONS[key]
            else:
                label = f"{(suffix or name).replace('-', ' ').title()} 폼"
                emoji, style = "🔄", discord.ButtonStyle.secondary
            self._add(name, f"{emoji} {label}", style)

    def _add(self, target, label, style):
        if target in self._targets or len(self.children) >= self.MAX_BUTTONS:
            return
        btn = discord.ui.Button(label=label[:80], style=style)
        btn.callback = self._make_callback(target)
        self.add_item(btn)
        self._targets.add(target)

    def _make_callback(self, target):
        async def callback(interaction: discord.Interaction):
            try:
                await interaction.response.defer()
                result = await build_pokemon_message(target)
                if not result:
                    await interaction.followup.send("폼/진화 정보를 불러올 수 없습니다.", ephemeral=True)
                    return
                embed, new_view = result
                new_view.message = interaction.message
                await interaction.edit_original_response(embed=embed, view=new_view)
                self.stop()  # 이전 뷰는 종료 (타임아웃 처리 안 함)
            except discord.HTTPException as e:
                if e.status == 429:
                    print("[경고] 버튼 클릭 중 디스코드 API 레이트 리밋(429) 발생.")
                else:
                    print(f"[오류] 상호작용 실패: {e}")
            except Exception:
                traceback.print_exc()
        return callback

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

# ==========================================
# 10. 디스코드 봇 / 이벤트 핸들러
# ==========================================
intents = discord.Intents.default()
intents.message_content = True

class PokemonBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self._cache_task = None

    async def setup_hook(self):
        global HTTP_SESSION, HTTP_SEM
        HTTP_SESSION = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "Mozilla/5.0 (PokemonDexBot)"},
        )
        HTTP_SEM = asyncio.Semaphore(10)
        # setup_hook은 최초 1회만 실행 → 재연결 시 중복 수집 없음
        self._cache_task = asyncio.create_task(build_korean_cache())

    async def close(self):
        if HTTP_SESSION and not HTTP_SESSION.closed:
            await HTTP_SESSION.close()
        await super().close()

client = PokemonBot()

@client.event
async def on_ready():
    print(f'🚀 도감 검색 전용 포켓몬 봇 가동 완료! ({client.user})')

@client.event
async def on_message(message):
    if message.author.bot:
        return

    parts = message.content.split(maxsplit=1)
    if not parts or parts[0] != '!도감':
        return

    try:
        if len(parts) < 2:
            await message.channel.send('사용법: `!도감 피카츄` / `!도감 25` / `!도감 charizard-mega-x`')
            return

        query = parts[1].strip()
        if re.search(r'[가-힣]', query) and not KOREAN_CACHE_READY:
            await message.channel.send('한국어 이름 데이터를 준비 중이에요. 잠시 후 다시 시도하거나 영어 이름/번호로 검색해 주세요!')
            return

        async with message.channel.typing():
            result = await build_pokemon_message(query)

        if not result:
            await message.channel.send('포켓몬을 찾을 수 없어요. 포켓몬 이름이나 번호를 정확히 입력해 주세요!')
            return

        embed, view = result
        view.message = await message.channel.send(embed=embed, view=view)

    except discord.HTTPException as e:
        if e.status == 429:
            print(f"[{message.author.name}] 요청 거부됨: 디스코드 API 레이트 리밋(429) 상태입니다.")
        else:
            print(f"[디스코드 에러] 메시지 전송 실패: {e}")
    except Exception:
        traceback.print_exc()

if __name__ == "__main__":
    token = os.environ.get('DISCORD_TOKEN')
    if not token:
        raise SystemExit("DISCORD_TOKEN 환경변수가 설정되지 않았습니다.")
    keep_alive()
    client.run(token)

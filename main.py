import discord
import os
import re
import asyncio
import aiohttp
import traceback
import unicodedata
import time
from collections import OrderedDict
from urllib.parse import quote
from io import BytesIO
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter
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
    biome_cells = []
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

        biome_cells.append(cells[col['biome']])
        times.update(extract_times(texts[col['time']]))

    # 카테고리 페이지 조회를 행마다 기다리지 않고 한꺼번에 병렬 처리
    for result in await asyncio.gather(*(expand_biome_cell(c) for c in biome_cells)):
        biomes.update(result)
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
        return ([], "정보 없음 (위키 연결 실패)", "Any")

    biomes, times, had_rows = set(), set(), False
    if status == 200:
        biomes, times, had_rows = await parse_normal_spawn_table(BeautifulSoup(html, "html.parser"))

    if not had_rows:
        l_biomes, l_times = await parse_legendary_spawn_table(title.replace('_', ' '))
        biomes |= l_biomes
        times |= l_times

    biome_list = sorted(biomes, key=str.lower)
    if biome_list:
        empty_text = None
    elif had_rows:
        empty_text = "바닐라 바이옴 없음 (모드 바이옴 전용)"
    else:
        empty_text = "정보 없음"

    sorted_times = [t for t in TIME_ORDER if t in times]
    time_text = ", ".join(sorted_times) if sorted_times else "Any"

    result = (biome_list, empty_text, time_text)
    PIXELMON_SPAWN_CACHE[species_name] = result
    return result

# ---------- 드랍 아이템 / 지닌 물건 ----------
PIXELMON_ITEMS_CACHE = {}
ITEM_KO_CACHE = {}

# 마인크래프트 바닐라 아이템 한글명 (PokeAPI에 없는 것들) — 필요하면 계속 추가
MC_ITEM_KO = {
    'sand': '모래', 'red sand': '붉은 모래', 'dirt': '흙', 'gravel': '자갈', 'clay ball': '점토 덩이',
    'clay': '점토', 'stone': '돌', 'cobblestone': '조약돌', 'obsidian': '흑요석', 'ice': '얼음',
    'packed ice': '꽁꽁 언 얼음', 'blue ice': '푸른 얼음', 'snowball': '눈덩이', 'snow block': '눈 블록',
    'bone': '뼈', 'bone meal': '뼛가루', 'string': '실', 'feather': '깃털', 'leather': '가죽',
    'gunpowder': '화약', 'slimeball': '슬라임볼', 'slime ball': '슬라임볼', 'ender pearl': '엔더 진주',
    'eye of ender': '엔더의 눈', 'blaze rod': '블레이즈 막대기', 'blaze powder': '블레이즈 가루',
    'magma cream': '마그마 크림', 'ghast tear': '가스트 눈물', 'coal': '석탄', 'charcoal': '숯',
    'iron ingot': '철 주괴', 'iron nugget': '철 조각', 'gold ingot': '금 주괴', 'gold nugget': '금 조각',
    'copper ingot': '구리 주괴', 'raw iron': '철 원석', 'raw gold': '금 원석', 'raw copper': '구리 원석',
    'diamond': '다이아몬드', 'emerald': '에메랄드', 'redstone': '레드스톤 가루', 'redstone dust': '레드스톤 가루',
    'glowstone dust': '발광석 가루', 'lapis lazuli': '청금석', 'nether quartz': '네더 석영', 'quartz': '네더 석영',
    'amethyst shard': '자수정 조각', 'flint': '부싯돌', 'stick': '막대기', 'paper': '종이', 'book': '책',
    'apple': '사과', 'golden apple': '황금 사과', 'carrot': '당근', 'potato': '감자', 'beetroot': '비트',
    'wheat': '밀', 'wheat seeds': '밀 씨앗', 'sugar': '설탕', 'sugar cane': '사탕수수', 'egg': '달걀',
    'pumpkin': '호박', 'melon slice': '수박 조각', 'cocoa beans': '코코아 콩', 'sweet berries': '달콤한 열매',
    'glow berries': '발광 열매', 'honeycomb': '벌집 조각', 'honey bottle': '꿀이 든 병',
    'raw beef': '생 소고기', 'raw porkchop': '생 돼지고기', 'raw chicken': '생 닭고기', 'raw mutton': '생 양고기',
    'raw rabbit': '생 토끼고기', 'raw cod': '생 대구', 'raw fish': '생 대구', 'raw salmon': '생 연어',
    'pufferfish': '복어', 'tropical fish': '열대어', 'ink sac': '먹물 주머니', 'glow ink sac': '발광 먹물 주머니',
    'spider eye': '거미 눈', 'rotten flesh': '썩은 살점', 'rabbit hide': '토끼 가죽', "rabbit's foot": '토끼발',
    'prismarine shard': '프리즈머린 조각', 'prismarine crystals': '프리즈머린 수정',
    'nautilus shell': '앵무조개 껍데기', 'kelp': '켈프', 'seagrass': '해초', 'lily pad': '수련잎',
    'vines': '덩굴', 'vine': '덩굴', 'cactus': '선인장', 'bamboo': '대나무',
    'brown mushroom': '갈색 버섯', 'red mushroom': '빨간색 버섯', 'oak log': '참나무 원목',
    'netherrack': '네더랙', 'soul sand': '영혼 모래', 'scute': '거북 인갑', 'turtle scute': '거북 인갑',
    'phantom membrane': '팬텀 막', 'glass bottle': '유리병', 'bowl': '그릇', 'wool': '양털',
    'white wool': '하얀색 양털', 'cobweb': '거미줄', 'slime block': '슬라임 블록', 'torch': '횃불',
    'shulker shell': '셜커 껍데기', 'dragon breath': '드래곤의 숨결', 'chorus fruit': '후렴과',
    'nether wart': '네더 사마귀', 'firework star': '폭죽 별', 'experience bottle': '경험치 병',
}
DYE_COLORS_KO = {
    'white': '하얀색', 'orange': '주황색', 'magenta': '자홍색', 'light blue': '하늘색', 'yellow': '노란색',
    'lime': '연두색', 'pink': '분홍색', 'gray': '회색', 'light gray': '회백색', 'cyan': '청록색',
    'purple': '보라색', 'blue': '파란색', 'brown': '갈색', 'green': '초록색', 'red': '빨간색', 'black': '검은색',
}

async def translate_item(name):
    """아이템 영어 이름 → 한글 (마크 사전 → PokeAPI → 영어 그대로)"""
    key = name.strip().lower()
    if key in ITEM_KO_CACHE:
        return ITEM_KO_CACHE[key]
    if key in MC_ITEM_KO:
        return MC_ITEM_KO[key]
    if key.endswith(' dye') and key[:-4] in DYE_COLORS_KO:
        return f"{DYE_COLORS_KO[key[:-4]]} 염료"

    slug = re.sub(r"[^a-z0-9]+", "-", key.replace("'", "").replace("é", "e")).strip("-")
    data = await fetch_json(f"{POKEAPI}/item/{slug}")
    if data is None:
        return name  # PokeAPI에 없는 아이템 → 영어 그대로 (포켓몬별 결과는 따로 캐시됨)
    ko = next((n['name'] for n in data.get('names', [])
               if n['language']['name'] == 'ko' and n.get('name')), name)
    ITEM_KO_CACHE[key] = ko
    return ko

def _parse_item_table(soup, heading_names, name_col, has_qty):
    heading = next((t for t in soup.find_all(["h2", "h3"])
                    if t.get_text(" ", strip=True).lower() in heading_names), None)
    if not heading:
        return []
    table = heading.find_next("table")
    if not table:
        return []

    col = {name_col: 0, 'chance': 1, 'quantity': 2}
    rows = []
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        texts = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in cells]
        lowered = [t.lower() for t in texts]
        if name_col in lowered and "chance" in lowered:  # 헤더 행
            for k in col:
                if k in lowered:
                    col[k] = lowered.index(k)
            continue
        if len(cells) <= col['chance'] or not texts[col[name_col]]:
            continue
        qty = texts[col['quantity']] if has_qty and len(texts) > col['quantity'] else ""
        rows.append((texts[col[name_col]], texts[col['chance']], qty))
    return rows

async def get_pixelmon_items(species_name):
    """{'drops': [(이름, 확률, 수량)], 'held': [(이름, 확률, '')]} 또는 연결 실패 시 None"""
    if species_name in PIXELMON_ITEMS_CACHE:
        return PIXELMON_ITEMS_CACHE[species_name]
    encoded = quote(wiki_title(species_name), safe="_:'.-")
    status, html = await fetch_text(f"{WIKI_BASE}/wiki/{encoded}")
    if status == 0:
        return None
    result = {'drops': [], 'held': []}
    if status == 200:
        soup = BeautifulSoup(html, "html.parser")
        result['drops'] = _parse_item_table(soup, {"drops"}, 'drop', True)
        result['held'] = _parse_item_table(soup, {"held item", "held items"}, 'item', False)

    # 아이템 이름 한글화
    for k in ('drops', 'held'):
        names = await asyncio.gather(*(translate_item(n) for n, _, _ in result[k]))
        result[k] = [(ko, c, q) for ko, (_, c, q) in zip(names, result[k])]

    PIXELMON_ITEMS_CACHE[species_name] = result
    return result

def _disp_width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in text)

def _pad(text, width):
    return text + " " * max(0, width - _disp_width(text))

def _item_block(rows, show_qty):
    name_w = max(_disp_width(n) for n, _, _ in rows)
    lines = []
    for name, chance, qty in rows:
        line = f"{_pad(name, name_w)}  {chance:>5}"
        if show_qty and qty and qty not in ("—", "-"):
            line += f"  {qty}개"
        lines.append(line)
    return "```text\n" + "\n".join(lines) + "\n```"

def format_items(display_name, items):
    if items is None:
        return "⚠️ 픽셀몬 위키에 연결하지 못했어요. 잠시 후 다시 시도해 주세요."
    parts = [f"🎒 **{display_name} 아이템 정보**"]
    parts.append("**드랍 아이템**")
    parts.append(_item_block(items['drops'], True) if items['drops'] else "> 정보 없음")
    parts.append("**지닌 물건**")
    parts.append(_item_block(items['held'], False) if items['held'] else "> 정보 없음")
    return "\n".join(parts)[:2000]

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

# PokeAPI에 한국어 폼 이름이 없는 경우를 위한 수동 번역 (필요하면 계속 추가)
FORM_SUFFIX_KO = {
    'battle-bond': '유대변화',
    'own-tempo': '마이페이스',
    'totem': '주인 포켓몬',
    'zen': '달마 모드',
    'eternamax': '무한다이맥스',
    'starter': '파트너',
    'female': '암컷',
    'male': '수컷',
    'midday': '한낮의 모습',
    'midnight': '한밤중의 모습',
    'single-strike': '일격의 태세',
    'rapid-strike': '연격의 태세',
    'low-key': '로우한 모습',
    'amped': '하이한 모습',
    'power-construct': '스웜체인지',
}

FORM_KO_CACHE = {}

async def get_form_ko_name(pokemon_name):
    """PokeAPI pokemon-form의 한국어 폼 이름 (없으면 None)"""
    if pokemon_name in FORM_KO_CACHE:
        return FORM_KO_CACHE[pokemon_name]
    data = await fetch_json(f"{POKEAPI}/pokemon-form/{pokemon_name}")
    if data is None:
        return None  # 실패는 캐시하지 않음
    ko = next((n['name'] for n in data.get('form_names', [])
               if n['language']['name'] == 'ko' and n.get('name')), None)
    FORM_KO_CACHE[pokemon_name] = ko
    return ko

def match_suffix_ko(suffix):
    if not suffix:
        return None
    if suffix in FORM_SUFFIX_KO:
        return FORM_SUFFIX_KO[suffix]
    for key in sorted(FORM_SUFFIX_KO, key=len, reverse=True):
        if suffix.startswith(key + '-') or suffix.endswith('-' + key):
            return FORM_SUFFIX_KO[key]
    return None

def resolve_form_label(pokemon_name, species_name, form_ko):
    """
    폼 이름 결정 우선순위:
    1) FORM_TRANSLATIONS  2) PokeAPI 한국어 폼 이름  3) FORM_SUFFIX_KO  4) 영어 그대로
    반환: (label, emoji, style, key, source)
    """
    suffix = get_form_suffix(pokemon_name, species_name)
    key = match_form(suffix)
    if key:
        label, emoji, style = FORM_TRANSLATIONS[key]
        return label, emoji, style, key, 'dict'
    api_ko = form_ko.get(pokemon_name)
    if api_ko:
        return api_ko, "🔄", discord.ButtonStyle.secondary, None, 'api'
    fb = match_suffix_ko(suffix)
    if fb:
        return fb, "🔄", discord.ButtonStyle.secondary, None, 'fallback'
    eng = f"{(suffix or pokemon_name).replace('-', ' ').title()} 폼"
    return eng, "🔄", discord.ButtonStyle.secondary, None, 'english'

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
# 8. 정보 수집 + 이미지 카드 / (실패 시) 텍스트 embed
# ==========================================
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_FILES = {
    'regular': os.path.join(FONT_DIR, "NanumGothic.ttf"),
    'bold': os.path.join(FONT_DIR, "NanumGothicBold.ttf"),
    'heavy': os.path.join(FONT_DIR, "NanumGothicExtraBold.ttf"),
}
FONT_INDEX = 0  # .ttc 폰트를 쓸 때만 바꾸면 됨
CARD_ENABLED = all(os.path.exists(p) for p in FONT_FILES.values())
_FONT_CACHE = {}

def card_font(weight, size):
    key = (weight, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(FONT_FILES[weight], size, index=FONT_INDEX)
    return _FONT_CACHE[key]

TYPE_INFO = {  # 영어 타입 → (한글, 배지 색)
    'normal': ('노말', '#9a9c96'), 'fire': ('불꽃', '#ee8130'), 'water': ('물', '#4f8ff7'),
    'grass': ('풀', '#6cbf45'), 'electric': ('전기', '#f2c522'), 'ice': ('얼음', '#7fd4d0'),
    'fighting': ('격투', '#c22e28'), 'poison': ('독', '#a33ea1'), 'ground': ('땅', '#d9b45a'),
    'flying': ('비행', '#7fb2ec'), 'psychic': ('에스퍼', '#f95587'), 'bug': ('벌레', '#9cb11a'),
    'rock': ('바위', '#b6a136'), 'ghost': ('고스트', '#735797'), 'dragon': ('드래곤', '#6f35fc'),
    'dark': ('악', '#5a5366'), 'steel': ('강철', '#8f9bb3'), 'fairy': ('페어리', '#d685ad'),
}
STAT_ROWS = [('hp', 'HP'), ('attack', '공격'), ('defense', '방어'),
             ('special-attack', '특공'), ('special-defense', '특방'), ('speed', '스피드')]

ART_CACHE = OrderedDict()

async def fetch_bytes(url):
    if not url:
        return None
    if url in ART_CACHE:
        ART_CACHE.move_to_end(url)
        return ART_CACHE[url]
    async with HTTP_SEM:
        try:
            async with HTTP_SESSION.get(url) as r:
                if r.status != 200:
                    return None
                raw = await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None
    ART_CACHE[url] = raw
    if len(ART_CACHE) > 200:
        ART_CACHE.popitem(last=False)
    return raw

def make_display_name(data, species_data, form_ko):
    display_name = ko_name(species_data['name'])
    for entry in species_data.get('names', []):
        if entry['language']['name'] == 'ko':
            display_name = entry['name']
            break

    if not data.get('is_default', True):
        label, _, _, key, source = resolve_form_label(data['name'], species_data['name'], form_ko)
        if key and key in PREFIX_FORMS:
            display_name = f"{label.replace(' 폼', '').replace('진화', '')} {display_name}"
        elif source == 'api' and display_name in label:
            display_name = label  # 예: '지우개굴닌자'
        else:
            display_name = f"{display_name} ({label})"
    return display_name

def get_image_url(data):
    sprites = data.get('sprites', {})
    return (sprites.get('other', {}).get('official-artwork', {}).get('front_default')
            or sprites.get('front_default'))

def assemble_info(data, species_data, form_ko, spawn, extra, art):
    (biomes, biome_empty, time_text), (ability, hidden, egg, hatch, catch) = spawn, extra
    display_name = make_display_name(data, species_data, form_ko)
    image_url = get_image_url(data)
    rarity = 'legendary' if species_data.get('is_legendary') else (
             'mythical' if species_data.get('is_mythical') else None)

    return {
        'no': species_data['id'], 'name': display_name, 'file_name': data['name'],
        'types': [t['type']['name'] for t in data['types']],
        'ability': ability, 'hidden': hidden, 'egg': egg, 'hatch': hatch, 'catch': catch,
        'biomes': biomes, 'biome_empty': biome_empty, 'time': time_text,
        'stats': {s['stat']['name']: s['base_stat'] for s in data['stats']},
        'rarity': rarity, 'image_url': image_url, 'art': art,
    }

# ---------- 이미지 카드 ----------
CARD_BG = "#14161f"
PANEL = "#1e2230"

def _hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def _text_color_on(hex_color):
    r, g, b = _hex_to_rgb(hex_color)
    return "#1b1b1b" if (0.299 * r + 0.587 * g + 0.114 * b) > 170 else "#ffffff"

def _fit(draw, text, weight, size, max_w, min_size=12):
    """max_w 안에 들어가도록 글자 크기를 줄이고, 그래도 넘치면 … 처리"""
    while size > min_size and draw.textlength(text, font=card_font(weight, size)) > max_w:
        size -= 1
    font = card_font(weight, size)
    if draw.textlength(text, font=font) > max_w:
        while text and draw.textlength(text + "…", font=font) > max_w:
            text = text[:-1]
        text += "…"
    return text, font

def _wrap_items(draw, items, font, max_w):
    lines, cur = [], ""
    for i, it in enumerate(items):
        piece = it + ("," if i < len(items) - 1 else "")
        trial = (cur + " " + piece).strip()
        if not cur or draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = piece
    if cur:
        lines.append(cur)
    return lines

def _stat_color(v):
    if v < 50:  return "#f25f5c"
    if v < 80:  return "#f7a35c"
    if v < 100: return "#f2d15c"
    if v < 120: return "#7ed37a"
    return "#4fc3f7"

def render_card(info):
    """정보 dict → PNG BytesIO (CPU 작업이라 to_thread로 호출)"""
    S, W = 2, 1000
    MAX_BIOME_LINES = 6
    def p(*v): return [round(x * S) for x in v]

    measure = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    biome_font = card_font('bold', 19 * S)
    if info['biomes']:
        lines = _wrap_items(measure, info['biomes'], biome_font, 888 * S)
        if len(lines) > MAX_BIOME_LINES:
            lines = lines[:MAX_BIOME_LINES]
            lines[-1] = lines[-1].rstrip(",") + " …"
    else:
        lines = [info['biome_empty'] or "정보 없음"]
    line_h = 28
    by = 425
    biome_h = 34 + line_h * len(lines)
    sy = by + biome_h + 22
    H = sy + 40 + 3 * 36 + 24

    img = Image.new("RGB", (W * S, H * S), CARD_BG)

    # 상단 타입색 그라데이션
    c1 = TYPE_INFO.get(info['types'][0], ('', '#6d7fa8'))[1]
    c2 = TYPE_INFO.get(info['types'][-1], ('', '#6d7fa8'))[1]
    bh = 230 * S
    band = Image.composite(Image.new("RGB", (W * S, bh), c2), Image.new("RGB", (W * S, bh), c1),
                           Image.linear_gradient("L").rotate(90).resize((W * S, bh)))
    img.paste(Image.composite(Image.new("RGB", (W * S, bh), CARD_BG), band,
                              Image.linear_gradient("L").resize((W * S, bh))), (0, 0))

    # 일러스트 뒤 은은한 빛
    glow = Image.new("L", img.size, 0)
    ImageDraw.Draw(glow).ellipse(p(70, 70, 370, 370), fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(45 * S))
    img.paste(Image.new("RGB", img.size, c1), (0, 0), glow)

    # 공식 일러스트
    d = ImageDraw.Draw(img)
    art_ok = False
    if info.get('art'):
        try:
            art = Image.open(BytesIO(info['art'])).convert("RGBA")
            art.thumbnail((320 * S, 320 * S), Image.LANCZOS)
            ax = 220 * S - art.width // 2
            ay = 220 * S - art.height // 2
            img.paste(art, (ax, ay), art)
            art_ok = True
        except Exception:
            pass
    d = ImageDraw.Draw(img)
    if not art_ok:
        d.rounded_rectangle(p(70, 70, 370, 370), radius=28 * S, outline="#6d7fa8", width=2 * S)
        d.text(p(220, 220), "이미지 없음", font=card_font('regular', 22 * S), fill="#b9c4e6", anchor="mm")

    # 번호 / 등급 / 이름 / 타입
    x0 = 420
    no_text = f"No.{info['no']:04d}"
    d.text(p(x0, 52), no_text, font=card_font('bold', 22 * S), fill="#dfe6ff")
    if info['rarity']:
        label, color = ("전설", "#d4a017") if info['rarity'] == 'legendary' else ("환상", "#9b59b6")
        nx = x0 + measure.textlength(no_text, font=card_font('bold', 22 * S)) / S + 14
        lw = measure.textlength(label, font=card_font('bold', 17 * S)) / S
        d.rounded_rectangle(p(nx, 52, nx + lw + 24, 80), radius=14 * S, fill=color)
        d.text(p(nx + 12, 66), label, font=card_font('bold', 17 * S), fill="#ffffff", anchor="lm")

    name_text, name_font = _fit(measure, info['name'], 'heavy', 56 * S, 540 * S, min_size=28 * S)
    d.text(p(x0, 88), name_text, font=name_font, fill="#ffffff")

    tx = x0
    type_font = card_font('bold', 22 * S)
    for t in info['types']:
        t_ko, t_color = TYPE_INFO.get(t, (t.title(), '#6d7fa8'))
        tw = measure.textlength(t_ko, font=type_font) / S
        d.rounded_rectangle(p(tx, 162, tx + tw + 36, 198), radius=18 * S, fill=t_color)
        d.text(p(tx + 18, 180), t_ko, font=type_font, fill=_text_color_on(t_color), anchor="lm")
        tx += tw + 48

    # 정보 칸 (2열 x 3행)
    cells = [("특성", info['ability']), ("숨겨진 특성", info['hidden']),
             ("알 그룹", info['egg']), ("포획률", info['catch']),
             ("부화 걸음 수", info['hatch']), ("출몰 시간", info['time'])]
    for i, (k, v) in enumerate(cells):
        cx = x0 + (i % 2) * 280
        cy = 225 + (i // 2) * 64
        d.rounded_rectangle(p(cx, cy, cx + 265, cy + 54), radius=12 * S, fill=PANEL)
        d.text(p(cx + 16, cy + 9), k, font=card_font('regular', 15 * S), fill="#8d96b3")
        v = str(v)
        max_w = 235 * S
        if measure.textlength(v, font=card_font('bold', 16 * S)) <= max_w:
            # 한 줄로 충분하면 19 → 16 사이에서 최대 크기로
            v_text, v_font = _fit(measure, v, 'bold', 19 * S, max_w, min_size=16 * S)
            d.text(p(cx + 16, cy + 28), v_text, font=v_font, fill="#ffffff")
        else:
            # 길면 쉼표 기준 두 줄로 (그래도 넘치면 … 처리)
            small = card_font('bold', 14 * S)
            two = _wrap_items(measure, [x.strip() for x in v.split(',')], small, max_w)
            if len(two) > 2:
                two = [two[0], _fit(measure, " ".join(two[1:]), 'bold', 14 * S, max_w, 14 * S)[0]]
            for j, ln in enumerate(two):
                d.text(p(cx + 16, cy + 25 + j * 15), ln, font=small, fill="#ffffff")

    # 출몰 바이옴
    d.rounded_rectangle(p(40, by, 960, by + biome_h), radius=12 * S, fill=PANEL)
    title = f"출몰 바이옴 ({len(info['biomes'])}곳)" if info['biomes'] else "출몰 바이옴"
    d.text(p(56, by + 9), title, font=card_font('regular', 15 * S), fill="#8d96b3")
    for i, ln in enumerate(lines):
        d.text(p(56, by + 30 + i * line_h), ln, font=biome_font, fill="#ffffff")

    # 종족치
    stats = info['stats']
    total = sum(stats.values())
    d.text(p(40, sy), "종족치", font=card_font('bold', 20 * S), fill="#ffffff")
    d.text(p(960, sy), f"합계 {total}", font=card_font('bold', 20 * S), fill="#ffffff", anchor="ra")
    for i, (key, label) in enumerate(STAT_ROWS):
        v = stats.get(key, 0)
        bx = 40 + (i % 2) * 470
        yy = sy + 40 + (i // 2) * 36
        d.text(p(bx, yy + 11), label, font=card_font('bold', 17 * S), fill="#c7cde0", anchor="lm")
        d.text(p(bx + 110, yy + 11), str(v), font=card_font('bold', 17 * S), fill="#ffffff", anchor="rm")
        bar_x, bar_w = bx + 125, 320
        d.rounded_rectangle(p(bar_x, yy + 4, bar_x + bar_w, yy + 18), radius=7 * S, fill="#262b3b")
        fw = min(bar_w, max(14, bar_w * v / 180))
        d.rounded_rectangle(p(bar_x, yy + 4, bar_x + fw, yy + 18), radius=7 * S, fill=_stat_color(v))

    out = BytesIO()
    img.resize((W, H), Image.LANCZOS).save(out, format="PNG", compress_level=1)  # optimize=True는 0.3초 걸려서 끔
    out.seek(0)
    return out

# ---------- 텍스트 embed (이미지 생성 실패 시 대체) ----------
def make_embed(info):
    stats = info['stats']
    lines = []
    for key, label in [('hp', 'HP '), ('attack', 'Atk'), ('defense', 'Def'),
                       ('special-attack', 'SpA'), ('special-defense', 'SpD'), ('speed', 'Spe')]:
        v = stats.get(key, 0)
        lines.append(f"{label} {v:>3} " + '█' * max(1, round(v / 255 * 12)))
    lines.append(f"Tot {sum(stats.values()):>3}")

    badge, color = "", 0x3498db
    if info['rarity'] == 'legendary':
        badge, color = "🌟 전설 ", 0xF1C40F
    elif info['rarity'] == 'mythical':
        badge, color = "✨ 환상 ", 0x9B59B6

    biome_text = ", ".join(info['biomes']) or info['biome_empty'] or "정보 없음"
    if len(biome_text) > 1000:
        biome_text = biome_text[:997] + "..."

    embed = discord.Embed(title=f"{badge}포켓몬 정보: {info['name']}", color=color)
    if info['image_url']:
        embed.set_thumbnail(url=info['image_url'])
    embed.add_field(name="📖 도감 번호", value=f"No.{info['no']}", inline=False)
    embed.add_field(name="⚔️ 타입", value=", ".join(TYPE_KO.get(t, t) for t in info['types']), inline=True)
    embed.add_field(name="🛡️ 특성", value=info['ability'], inline=True)
    embed.add_field(name="🔒 숨겨진 특성", value=info['hidden'], inline=True)
    embed.add_field(name="🥚 알 그룹", value=info['egg'], inline=True)
    embed.add_field(name="🎯 포획률", value=info['catch'], inline=True)
    embed.add_field(name="🐣 부화 걸음 수", value=info['hatch'], inline=True)
    embed.add_field(name="🍄 출몰 바이옴", value=biome_text, inline=True)
    embed.add_field(name="⏳ 출몰 시간", value=info['time'], inline=True)
    embed.add_field(name="🌸 종족치", value="```text\n" + "\n".join(lines) + "\n```", inline=False)
    return embed

async def render_png(info):
    """이미지 카드 PNG 바이트. 폰트가 없거나 실패하면 None (→ 텍스트 embed로 대체)"""
    if not CARD_ENABLED:
        return None
    try:
        return (await asyncio.to_thread(render_card, info)).getvalue()
    except Exception:
        traceback.print_exc()
        return None

def edit_kwargs(payload):
    """메시지 수정 시: 이미지↔embed 전환을 모두 처리"""
    if 'file' in payload:
        return {'attachments': [payload['file']], 'embed': None}
    return {'embed': payload['embed'], 'attachments': []}

CARD_PNG_CACHE = OrderedDict()  # 완성된 카드 이미지 (포켓몬 이름 → PNG 바이트)

async def fetch_pokemon_and_species(query):
    q = query.strip().lower()
    sid = None
    if re.search(r'[가-힣]', q):
        sid = POKEMON_KO_TO_ID.get(q.replace(' ', ''))
        if not sid:
            return None, None
    elif q.isdigit():
        sid = q

    if sid:
        # 번호를 이미 알면 포켓몬/종 정보를 동시에 요청
        data, species_data = await asyncio.gather(
            fetch_json(f"{POKEAPI}/pokemon/{sid}"), fetch_json(f"{POKEAPI}/pokemon-species/{sid}"))
        if data and (not species_data or species_data['name'] != data['species']['name']):
            species_data = await fetch_json(data['species']['url'])
        return data, species_data

    data = await fetch_pokemon(query)
    if not data:
        return None, None
    return data, await fetch_json(data['species']['url'])

async def _timed(name, coro, timings):
    t = time.perf_counter()
    try:
        return await coro
    finally:
        timings[name] = time.perf_counter() - t

def _log_timings(pokemon_name, timings, total):
    parts = " | ".join(f"{k} {v:.2f}s" for k, v in timings.items())
    print(f"[⏱ {pokemon_name}] 총 {total:.2f}s | {parts}")

async def build_pokemon_message(query):
    """(payload, view) 또는 None"""
    t_start = time.perf_counter()
    timings = {}
    data, species_data = await _timed("포켓몬/종", fetch_pokemon_and_species(query), timings)
    if not data or not species_data:
        return None

    async def get_chain():
        url = (species_data.get('evolution_chain') or {}).get('url')
        if not url:
            return None
        chain_json = await fetch_json(url)
        return chain_json['chain'] if chain_json else None

    async def get_form_names():
        names = {v['pokemon']['name'] for v in species_data.get('varieties', [])}
        names.add(data['name'])
        names = [n for n in names if get_form_suffix(n, species_data['name'])]
        results = await asyncio.gather(*(get_form_ko_name(n) for n in names))
        return {n: ko for n, ko in zip(names, results) if ko}

    cached_png = CARD_PNG_CACHE.get(data['name']) if CARD_ENABLED else None
    need_art = CARD_ENABLED and cached_png is None

    # 진화 / 폼 이름 / 스폰 / 특성 / 일러스트를 전부 동시에 요청
    chain, form_ko, spawn, extra, art = await asyncio.gather(
        _timed("진화", get_chain(), timings),
        _timed("폼이름", get_form_names(), timings),
        _timed("위키", get_pixelmon_spawn_info(species_data['name']), timings),
        _timed("특성", get_pokemon_extra_info(data, species_data), timings),
        _timed("일러스트", fetch_bytes(get_image_url(data)) if need_art else asyncio.sleep(0, result=None), timings),
    )
    view = PokemonView(species_data, data, chain, form_ko)

    if cached_png is not None:
        CARD_PNG_CACHE.move_to_end(data['name'])
        _log_timings(data['name'] + " (캐시)", timings, time.perf_counter() - t_start)
        return {'file': discord.File(BytesIO(cached_png), filename=f"{data['name']}.png")}, view

    info = assemble_info(data, species_data, form_ko, spawn, extra, art)
    png = await _timed("렌더", render_png(info), timings)
    _log_timings(data['name'], timings, time.perf_counter() - t_start)
    if png is None:
        return {'embed': make_embed(info)}, view

    # 일러스트나 위키 정보가 빠진 불완전한 카드는 캐시하지 않음
    if info['art'] is not None and spawn[1] != "정보 없음 (위키 연결 실패)":
        CARD_PNG_CACHE[data['name']] = png
        if len(CARD_PNG_CACHE) > 150:
            CARD_PNG_CACHE.popitem(last=False)
    payload = {'file': discord.File(BytesIO(png), filename=f"{data['name']}.png")}
    return payload, view

# ==========================================
# 9. 진화 및 폼 체인지 버튼 뷰
# ==========================================
class PokemonView(discord.ui.View):
    MAX_BUTTONS = 24  # 1칸은 드랍 아이템 버튼용으로 남겨둠

    def __init__(self, species_data, pokemon_data, chain, form_ko=None):
        super().__init__(timeout=180)
        self.message = None
        self._targets = set()
        self.species_name = species_data['name']

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
            label, emoji, style, _, _ = resolve_form_label(name, species_data['name'], form_ko or {})
            self._add(name, f"{emoji} {label}", style)

        # 4. 드랍 아이템 버튼 (항상 표시)
        drop_btn = discord.ui.Button(label="🎒 아이템 정보", style=discord.ButtonStyle.success)
        drop_btn.callback = self._on_drops_click
        self.add_item(drop_btn)

    async def _on_drops_click(self, interaction: discord.Interaction):
        try:
            # 누른 사람에게만 보이는 메시지 (채널이 지저분해지지 않게)
            await interaction.response.defer(ephemeral=True, thinking=True)
            items = await get_pixelmon_items(self.species_name)
            await interaction.followup.send(format_items(ko_name(self.species_name), items), ephemeral=True)
        except discord.HTTPException as e:
            print(f"[오류] 드랍 아이템 표시 실패: {e}")
        except Exception:
            traceback.print_exc()

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
                payload, new_view = result
                new_view.message = interaction.message
                await interaction.edit_original_response(view=new_view, **edit_kwargs(payload))
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

        payload, view = result
        view.message = await message.channel.send(view=view, **payload)

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
    if not CARD_ENABLED:
        print(f"⚠️ 폰트 파일이 없어 이미지 카드 대신 텍스트로 표시합니다. ({FONT_DIR} 폴더 확인)")
    keep_alive()
    client.run(token)

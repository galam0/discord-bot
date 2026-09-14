import discord
import os
import requests
import aiohttp
import asyncio
import re
from bs4 import BeautifulSoup
from pymongo import MongoClient
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
    t = Thread(target=run_server)
    t.start()

# ==========================================
# 2. 디스코드 봇 및 데이터베이스 설정
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

mongo_uri = os.environ.get('MONGODB_URI')
mongo_client = MongoClient(mongo_uri)
db = mongo_client['pokemon_game']
collection = db['users']

POKEMON_KO_TO_ID = {}

TYPE_KO = {
    'normal': '⚪ 노말', 'fire': '🔥 불꽃', 'water': '💧 물',
    'grass': '🌿 풀', 'electric': '⚡ 전기', 'ice': '❄️ 얼음',
    'fighting': '🥊 격투', 'poison': '☠️ 독', 'ground': '⏳ 땅',
    'flying': '🕊️ 비행', 'psychic': '🔮 에스퍼', 'bug': '🐛 벌레',
    'rock': '🪨 바위', 'ghost': '👻 고스트', 'dragon': '🐉 드래곤',
    'steel': '⚙️ 강철', 'fairy': '✨ 페어리', 'dark': '🌙 악'
}

# 특성 한국어 캐시
ABILITY_KO_CACHE = {}

# 알 그룹 한국어 이름
EGG_GROUP_KO = {
    'monster': '괴수',
    'water1': '수중 1',
    'water2': '수중 2',
    'water3': '수중 3',
    'bug': '벌레',
    'flying': '비행',
    'field': '육상',
    'ground': '육상',
    'fairy': '요정',
    'grass': '식물',
    'human-like': '인간형',
    'mineral': '광물',
    'amorphous': '부정형',
    'dragon': '드래곤',
    'undiscovered': '미발견',
}

async def get_ability_korean_name(session, ability_name):
    """PokeAPI에서 특성의 한국어 이름을 가져옵니다."""
    key = ability_name.lower()

    if key in ABILITY_KO_CACHE:
        return ABILITY_KO_CACHE[key]

    try:
        url = f"https://pokeapi.co/api/v2/ability/{key}"
        async with session.get(url, timeout=8) as response:
            if response.status == 200:
                data = await response.json()

                for name_entry in data.get('names', []):
                    if name_entry.get('language', {}).get('name') == 'ko':
                        korean_name = name_entry.get('name')
                        if korean_name:
                            ABILITY_KO_CACHE[key] = korean_name
                            return korean_name
    except Exception:
        pass

    fallback = ability_name.replace('-', ' ').title()
    ABILITY_KO_CACHE[key] = fallback
    return fallback

async def get_pokemon_extra_info(data, species_data):
    """
    일반 특성과 숨겨진 특성을 분리하여 구성합니다.
    """
    normal_abilities = []
    hidden_abilities = []

    async with aiohttp.ClientSession() as session:
        for ability_entry in data.get('abilities', []):
            ability_name = ability_entry.get('ability', {}).get('name')
            is_hidden = ability_entry.get('is_hidden', False)

            if ability_name:
                korean_name = await get_ability_korean_name(session, ability_name)
                if is_hidden:
                    if korean_name not in hidden_abilities:
                        hidden_abilities.append(korean_name)
                else:
                    if korean_name not in normal_abilities:
                        normal_abilities.append(korean_name)

    normal_text = ", ".join(normal_abilities) if normal_abilities else "정보 없음"
    hidden_text = ", ".join(hidden_abilities) if hidden_abilities else "없음"

    if hidden_text != "없음":
        ability_text = f"{normal_text}\n(숨겨진: {hidden_text})"
    else:
        ability_text = normal_text

    egg_groups = []
    for group in species_data.get('egg_groups', []):
        group_name = group.get('name', '')
        korean_name = EGG_GROUP_KO.get(
            group_name,
            group_name.replace('-', ' ').title()
        )

        if korean_name and korean_name not in egg_groups:
            egg_groups.append(korean_name)

    egg_group_text = ", ".join(egg_groups) if egg_groups else "미발견"

    hatch_counter = species_data.get('hatch_counter')
    if hatch_counter is not None:
        hatch_steps = hatch_counter * 255
        hatch_text = f"{hatch_steps:,}"
    else:
        hatch_text = "정보 없음"

    capture_rate = species_data.get('capture_rate')
    catch_text = str(capture_rate) if capture_rate is not None else "정보 없음"

    return ability_text, egg_group_text, hatch_text, catch_text

# ==========================================
# 3. 픽셀몬 위키 스폰 바이옴/시간 추출
# ==========================================
PIXELMON_SPAWN_CACHE = {}

PIXELMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

TIME_ORDER = [
    "Dawn", "Morning", "Day", "Midday", "Afternoon",
    "Dusk", "Night", "Midnight", "Any"
]


def normalize_wiki_name(name):
    """위키에서 비교하기 쉽도록 이름을 정규화합니다."""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def clean_biome_text(text):
    """바이옴 셀의 불필요한 표기를 정리하고 보기 쉽게 다듬습니다."""
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text).strip()

    # Pixelmon 위키의 Forge 표기 및 불필요한 태그 제거
    text = re.sub(r"\s*\(Forge\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(Mod\)", "", text, flags=re.IGNORECASE)

    # 자주 쓰이는 영문 바이옴 이름을 한글/친숙한 명칭으로 매핑 (원하시면 영어 유지 채택 가능)
    biome_mapping = {
        "birches": "Birch Forest (자작나무 숲)",
        "plains": "Plains (평원)",
        "forest": "Forest (숲)",
        "dark forest": "Dark Forest (어두운 숲)",
        "desert": "Desert (사막)",
        "jungle": "Jungle (정글)",
        "taiga": "Taiga (타이거)",
        "swamp": "Swamp (습지)",
        "beach": "Beach (해변)",
        "mountain": "Mountain (산)",
        "savanna": "Savanna (사바나)",
        "ocean": "Ocean (바다)",
        "river": "River (강)",
        "snowy": "Snowy Biome (눈 내리는 지형)",
        "badlands": "Badlands (악지)",
        "mushroom": "Mushroom Fields (버섯 섬)"
    }

    low_text = text.lower()
    for eng, friendly in biome_mapping.items():
        if low_text == eng:
            return friendly

    if low_text == "birches":
        text = "Birch Forest"

    return text


def extract_times(text):
    """시간 셀에서 Pixelmon 출몰 시간을 안정적으로 추출합니다."""
    found = []
    text = text or ""

    for tk in TIME_ORDER:
        if re.search(rf"\b{re.escape(tk)}\b", text, re.IGNORECASE):
            found.append(tk)

    # 표에서 '-' / 빈 값은 특정 시간이 아니라 Any로 취급
    if not found and text.strip() in {"", "-", "—", "–"}:
        return []

    return found


def parse_normal_spawn_table(soup):
    """
    일반 포켓몬 페이지의 'Spawn Biomes' 표만 정확하게 읽습니다.

    현재 Pixelmon 위키 구조:
        Biome | ModID | Time | Location | Condition | Rarity

    예전 코드처럼 모든 표를 훑고 열 번호를 추측하지 않습니다.
    그렇게 하면 Drops/Stats/Move 등의 표에서 엉뚱한 문자열이
    바이옴으로 들어가는 문제가 생깁니다.
    """
    biomes = set()
    times = set()

    heading = None
    for tag in soup.find_all(["h2", "h3"]):
        heading_text = tag.get_text(" ", strip=True).lower()
        if "spawn biomes" in heading_text:
            heading = tag
            break

    if not heading:
        return biomes, times

    table = heading.find_next("table")
    if not table:
        return biomes, times

    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 3:
            continue

        values = [cell.get_text(" ", strip=True) for cell in cells]

        # 헤더
        if values[0].strip().lower() == "biome":
            continue

        biome = clean_biome_text(values[0])
        time_text = values[2] if len(values) > 2 else ""

        if biome:
            biomes.add(biome)

        times.update(extract_times(time_text))

    return biomes, times


async def parse_legendary_spawn_table(session, english_name):
    """
    전설/환상 포켓몬은 개별 포켓몬 페이지에 Spawn Biomes 표가 없고
    Legendary_Pokemon 페이지의 Locations 표를 참조하므로 여기서 보완합니다.

    표 구조:
        Pokémon | Biomes | ModID | Time | Location | Condition | Rarity

    같은 포켓몬의 바이옴이 여러 줄로 이어지는 경우도 처리합니다.
    """
    url = "https://pixelmonmod.com/wiki/Legendary_Pokemon"
    target = normalize_wiki_name(english_name)

    try:
        async with session.get(url, headers=PIXELMON_HEADERS, timeout=12) as response:
            if response.status != 200:
                return set(), set()

            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")

            biomes = set()
            times = set()
            matched = False

            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                if not rows:
                    continue

                active_match = False

                for row in rows:
                    cells = row.find_all(["th", "td"], recursive=False)
                    if len(cells) < 4:
                        continue

                    values = [cell.get_text(" ", strip=True) for cell in cells]
                    first = normalize_wiki_name(values[0])

                    # 헤더
                    if first in {"pokemon", "pokémon"}:
                        active_match = False
                        continue

                    # 새 포켓몬 행이면 기존 continuation을 종료
                    if first:
                        active_match = (first == target)
                        if active_match:
                            matched = True

                    if not active_match:
                        continue

                    # Pokémon | Biomes | ModID | Time | ...
                    if len(values) > 1:
                        biome = clean_biome_text(values[1])
                        if biome:
                            biomes.add(biome)

                    if len(values) > 3:
                        times.update(extract_times(values[3]))

                if matched:
                    # 같은 표에서 해당 포켓몬을 찾았으면 다른 세대 표는 볼 필요 없음
                    break

            return biomes, times

    except Exception:
        return set(), set()


async def get_pixelmon_spawn_info(english_name):
    """일반/전설/환상 포켓몬의 Pixelmon 출몰 바이옴과 시간을 반환합니다."""
    cache_key = normalize_wiki_name(english_name)
    if cache_key in PIXELMON_SPAWN_CACHE:
        return PIXELMON_SPAWN_CACHE[cache_key]

    # Pixelmon 위키의 문서명은 일반적으로 첫 글자만 대문자입니다.
    formatted_name = english_name.replace(" ", "_").capitalize()
    wiki_url = f"https://pixelmonmod.com/wiki/{formatted_name}"

    try:
        async with aiohttp.ClientSession() as session:
            biomes = set()
            times = set()

            # --------------------------------------------------
            # 1차: 개별 포켓몬 페이지의 Spawn Biomes 표
            # --------------------------------------------------
            async with session.get(
                wiki_url,
                headers=PIXELMON_HEADERS,
                timeout=12
            ) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, "html.parser")
                    biomes, times = parse_normal_spawn_table(soup)

            # --------------------------------------------------
            # 2차: 전설/환상 포켓몬
            # 개별 페이지에는 Spawn rate만 있고 실제 위치 정보가
            # Legendary Pokémon 페이지에 있는 경우가 있습니다.
            # --------------------------------------------------
            if not biomes:
                legendary_biomes, legendary_times = await parse_legendary_spawn_table(
                    session,
                    english_name
                )
                biomes.update(legendary_biomes)
                times.update(legendary_times)

            sorted_times = [t for t in TIME_ORDER if t in times]

            # 특정 시간이 표에 없으면 Any
            time_text = ", ".join(sorted_times) if sorted_times else "Any"

            # 위키가 'Does Not Spawn'이라고 명시한 경우도 정보 없음으로 버리지 않음
            if biomes:
                sorted_biomes = sorted(biomes, key=str.lower)
                biome_text = ", ".join(sorted_biomes)
            else:
                biome_text = "정보 없음"

            if len(biome_text) > 1000:
                biome_text = biome_text[:997] + "..."

            result = (biome_text, time_text)
            PIXELMON_SPAWN_CACHE[cache_key] = result
            return result

    except Exception:
        result = ("정보 없음", "Any")
        PIXELMON_SPAWN_CACHE[cache_key] = result
        return result

# ==========================================
# 4. 한국어 사전 구축 (aiohttp)
# ==========================================
async def fetch_species_name(session, item):
    try:
        async with session.get(item['url']) as response:
            if response.status == 200:
                data = await response.json()
                p_id = str(data['id'])
                for n in data.get('names', []):
                    if n['language']['name'] == 'ko':
                        POKEMON_KO_TO_ID[n['name'].lower()] = p_id
                        break
    except Exception:
        pass

async def build_korean_cache():
    print('🔄 비동기로 전체 포켓몬 한국어 데이터베이스를 수집 중입니다...')
    async with aiohttp.ClientSession() as session:
        async with session.get('https://pokeapi.co/api/v2/pokemon-species?limit=1025') as response:
            if response.status == 200:
                data = await response.json()
                results = data['results']
                tasks = [fetch_species_name(session, item) for item in results]
                await asyncio.gather(*tasks)
    print(f"✅ 총 {len(POKEMON_KO_TO_ID)}마리 포켓몬의 한국어 이름 등록이 완료되었습니다!")

# ==========================================
# 5. 입력값 -> 도감 ID 변환
# ==========================================
def resolve_pokemon_id(query):
    query = query.strip().lower()

    if query.isdigit():
        return query

    if any(form in query for form in ['mega', 'alola', 'galar', 'hisui', 'paldea']):
        return query

    if query in POKEMON_KO_TO_ID:
        return POKEMON_KO_TO_ID[query]

    species_url = f'https://pokeapi.co/api/v2/pokemon-species/{query}'
    try:
        res = requests.get(species_url)
        if res.status_code == 200:
            return str(res.json()['id'])
    except Exception:
        pass

    return None

# ==========================================
# 6. 포켓몬 정보 및 스크래핑 통합 Embed 생성
# ==========================================
async def get_pokemon_embed_async(pokemon_id_or_name):
    query = str(pokemon_id_or_name).strip().lower()
    pokemon_id = resolve_pokemon_id(query)
    
    if not pokemon_id:
        return None, None

    species_name = query.split('-')[0] if '-' in query else pokemon_id
    species_url = f'https://pokeapi.co/api/v2/pokemon-species/{species_name}'
    species_res = requests.get(species_url)
    
    if species_res.status_code != 200:
        return None, None

    species_data = species_res.json()
    english_name = species_data['name']

    display_name = str(species_data['id'])
    for name_entry in species_data['names']:
        if name_entry['language']['name'] == 'ko':
            display_name = name_entry['name']
            break

    detail_url = f'https://pokeapi.co/api/v2/pokemon/{query}'
    detail_res = requests.get(detail_url)
    
    if detail_res.status_code != 200:
        detail_url = f'https://pokeapi.co/api/v2/pokemon/{pokemon_id}'
        detail_res = requests.get(detail_url)

    if detail_res.status_code != 200:
        return None, None
        
    data = detail_res.json()
    image_url = data['sprites']['other']['official-artwork']['front_default'] or data['sprites']['front_default']

    if 'alola' in query:
        display_name = f"알로라 {display_name}"
    elif 'galar' in query:
        display_name = f"가라르 {display_name}"
    elif 'hisui' in query:
        display_name = f"히스이 {display_name}"
    elif 'paldea' in query:
        display_name = f"팔데아 {display_name}"
    elif 'mega-x' in query:
        display_name = f"메가 {display_name} X"
    elif 'mega-y' in query:
        display_name = f"메가 {display_name} Y"
    elif 'mega' in query:
        display_name = f"메가 {display_name}"

    types_list = [TYPE_KO.get(t['type']['name'], t['type']['name'].capitalize()) for t in data['types']]
    types_str = ", ".join(types_list)

    stats = {stat['stat']['name']: stat['base_stat'] for stat in data['stats']}
    hp = stats.get('hp', 0)
    atk = stats.get('attack', 0)
    dfe = stats.get('defense', 0)
    spa = stats.get('special-attack', 0)
    spd = stats.get('special-defense', 0)
    spe = stats.get('speed', 0)
    total = hp + atk + dfe + spa + spd + spe

    stats_table = (
        "```text\n"
        "+-----------------------------------+\n"
        "|  HP  Atk  Def  SpA  SpD  Spe | Total |\n"
        "+-----------------------------------+\n"
        f"| {hp:>3}  {atk:>3}  {dfe:>3}  {spa:>3}  {spd:>3}  {spe:>3} |  {total:>3}  |\n"
        "+-----------------------------------+\n"
        "```"
    )

    biome_info, time_info = await get_pixelmon_spawn_info(english_name)
    ability_text, egg_group_text, hatch_text, catch_text = await get_pokemon_extra_info(data, species_data)

    # 전설 / 환상 여부는 PokeAPI species 데이터의 공식 분류를 사용합니다.
    # 지역 폼이나 다른 형태로 조회해도 species 데이터가 같다면 동일하게 표시됩니다.
    rarity_badge = ""
    rarity_color = 0xcc0000

    if species_data.get("is_legendary", False):
        rarity_badge = "🌟 전설"
        rarity_color = 0xF1C40F
    elif species_data.get("is_mythical", False):
        rarity_badge = "✨ 환상"
        rarity_color = 0x9B59B6

    title_prefix = f"{rarity_badge} " if rarity_badge else ""

    embed = discord.Embed(
        title=f"{title_prefix}포켓몬 정보: {display_name}",
        color=rarity_color
    )
    embed.set_thumbnail(url=image_url)

    embed.add_field(
        name="📖 도감 번호",
        value=f"No.{species_data['id']}",
        inline=False
    )

    # 전설/환상 포켓몬만 별도의 등급 뱃지를 표시합니다.
    if rarity_badge:
        embed.add_field(name="🏷️ 등급", value=f"**{rarity_badge}**", inline=False)

    embed.add_field(name="⚔️ 타입", value=types_str, inline=True)
    embed.add_field(name="🛡️ 특성", value=ability_text, inline=True)
    embed.add_field(name="🥚 알 그룹", value=egg_group_text, inline=True)

    embed.add_field(name="🐣 부화 걸음 수", value=hatch_text, inline=True)
    embed.add_field(name="🟣 포획률", value=catch_text, inline=True)
    embed.add_field(name="⏳ 출몰 시간", value=time_info, inline=True)

    embed.add_field(name="🍄 출몰 바이옴", value=biome_info, inline=True)
    embed.add_field(name="🌸 종족치", value=stats_table, inline=False)

    return embed, species_data

# ==========================================
# 7. 진화 / 메가진화 / 지역폼 / 원래형태 버튼 View 클래스
# ==========================================
class PokemonEvolutionView(discord.ui.View):
    def __init__(self, species_data, current_pokemon_name, base_species_name=None):
        super().__init__(timeout=120)
        self.species_data = species_data
        self.current_name = current_pokemon_name
        self.base_species_name = base_species_name or species_data['name']
        self.setup_buttons()

    def setup_buttons(self):
        if self.current_name != self.base_species_name:
            btn = discord.ui.Button(label="🔄 원래 형태", style=discord.ButtonStyle.secondary, custom_id=f"evo_{self.base_species_name}")
            btn.callback = self.on_evolution_click
            self.add_item(btn)

        evo_chain_url = self.species_data['evolution_chain']['url']
        chain_res = requests.get(evo_chain_url)
        
        if chain_res.status_code == 200:
            chain_data = chain_res.json()['chain']
            prev_evo, next_evos = self.find_evolutions(chain_data, self.species_data['name'])

            if prev_evo and prev_evo != self.current_name:
                btn = discord.ui.Button(label="이전 진화", style=discord.ButtonStyle.secondary, custom_id=f"evo_{prev_evo}")
                btn.callback = self.on_evolution_click
                self.add_item(btn)

            for next_evo in next_evos:
                if next_evo != self.current_name:
                    btn = discord.ui.Button(label=f"진화 ({next_evo.capitalize()})", style=discord.ButtonStyle.primary, custom_id=f"evo_{next_evo}")
                    btn.callback = self.on_evolution_click
                    self.add_item(btn)

        varieties = self.species_data.get('varieties', [])
        for var in varieties:
            var_name = var['pokemon']['name']
            if var_name == self.current_name:
                continue

            if 'mega' in var_name:
                label_name = "메가진화"
                if 'mega-x' in var_name: label_name = "메가진화 X"
                elif 'mega-y' in var_name: label_name = "메가진화 Y"
                btn = discord.ui.Button(label=f"✨ {label_name}", style=discord.ButtonStyle.success, custom_id=f"evo_{var_name}")
                btn.callback = self.on_evolution_click
                self.add_item(btn)
                
            elif 'alola' in var_name:
                btn = discord.ui.Button(label="🏝️ 알로라 폼", style=discord.ButtonStyle.success, custom_id=f"evo_{var_name}")
                btn.callback = self.on_evolution_click
                self.add_item(btn)
            elif 'galar' in var_name:
                btn = discord.ui.Button(label="🛡️ 가라르 폼", style=discord.ButtonStyle.success, custom_id=f"evo_{var_name}")
                btn.callback = self.on_evolution_click
                self.add_item(btn)
            elif 'hisui' in var_name:
                btn = discord.ui.Button(label="📜 히스이 폼", style=discord.ButtonStyle.success, custom_id=f"evo_{var_name}")
                btn.callback = self.on_evolution_click
                self.add_item(btn)
            elif 'paldea' in var_name:
                btn = discord.ui.Button(label="🥊 팔데아 폼", style=discord.ButtonStyle.success, custom_id=f"evo_{var_name}")
                btn.callback = self.on_evolution_click
                self.add_item(btn)

    def find_evolutions(self, chain_node, target_name):
        if chain_node['species']['name'] == target_name:
            next_evos = [child['species']['name'] for child in chain_node['evolves_to']]
            return None, next_evos

        for child in chain_node['evolves_to']:
            if child['species']['name'] == target_name:
                next_evos = [grandchild['species']['name'] for grandchild in child['evolves_to']]
                return child['species']['name'], next_evos
            
            for grandchild in child['evolves_to']:
                if grandchild['species']['name'] == target_name:
                    return child['species']['name'], []
        return None, []

    async def on_evolution_click(self, interaction: discord.Interaction):
        await interaction.response.defer()
        target_pokemon = interaction.data['custom_id'].replace('evo_', '')
        embed, new_species_data = await get_pokemon_embed_async(target_pokemon)

        if embed and new_species_data:
            new_view = PokemonEvolutionView(new_species_data, target_pokemon, self.base_species_name)
            await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=new_view)
        else:
            await interaction.followup.send("정보를 불러올 수 없습니다.", ephemeral=True)

# ==========================================
# 8. 디스코드 이벤트 핸들러 및 실행
# ==========================================
@client.event
async def on_ready():
    await build_korean_cache()
    print('🚀 포켓몬 봇이 완벽하게 가동되었습니다!')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content == '!도감':
        embed = discord.Embed(
            title="📖 포켓몬 도감 사용법",
            description="`!도감 [포켓몬 이름 또는 번호]` 형식으로 입력해 주세요!\n\n**예시:**\n• `!도감 나옹` / `!도감 리자몽` (지역 폼/메가진화 지원)\n• `!도감 52` (도감 번호 검색)",
            color=0x3498db
        )
        await message.channel.send(embed=embed)

    elif message.content.startswith('!도감 '):
        query = message.content.split(' ')[1].strip()
        embed, species_data = await get_pokemon_embed_async(query)

        if embed and species_data:
            base_name = species_data['name']
            view = PokemonEvolutionView(species_data, base_name, base_name)
            await message.channel.send(embed=embed, view=view)
        else:
            await message.channel.send('포켓몬을 찾을 수 없어요. 포켓몬 이름이나 번호를 정확히 입력해 주세요!')

    elif message.content.startswith('!포획 '):
        query = message.content.split(' ')[1].strip()
        embed, species_data = await get_pokemon_embed_async(query)

        if species_data:
            pokemon_name = str(species_data['id'])
            for name_entry in species_data['names']:
                if name_entry['language']['name'] == 'ko':
                    pokemon_name = name_entry['name']
                    break

            user_id = str(message.author.id)
            user_data = collection.find_one({"_id": user_id})
            
            if not user_data:
                collection.insert_one({"_id": user_id, "pokemon_list": [pokemon_name]})
                await message.channel.send(f"🎉 앗! 야생의 **{pokemon_name}**(을)를 잡았다! (첫 포획!)")
            else:
                if pokemon_name not in user_data["pokemon_list"]:
                    collection.update_one({"_id": user_id}, {"$push": {"pokemon_list": pokemon_name}})
                    await message.channel.send(f"🎉 앗! 야생의 **{pokemon_name}**(을)를 잡았다! (내 도감에 저장됨)")
                else:
                    await message.channel.send("이미 내 도감에 등록된 포켓몬입니다.")
        else:
            await message.channel.send("존재하지 않는 포켓몬입니다. 정확한 이름이나 번호를 입력해 주세요!")

    elif message.content == '!내포켓몬':
        user_id = str(message.author.id)
        user_data = collection.find_one({"_id": user_id})
        
        if user_data and len(user_data["pokemon_list"]) > 0:
            pokemon_list = ", ".join(user_data["pokemon_list"])
            await message.channel.send(f"📖 **{message.author.display_name}님의 포켓몬 도감**\n{pokemon_list}")
        else:
            await message.channel.send("아직 잡은 포켓몬이 없습니다.")

    elif message.content == '!순위':
        all_users = collection.find()
        ranking = []
        for user in all_users:
            count = len(user.get("pokemon_list", []))
            ranking.append((user["_id"], count))
        
        if not ranking:
            await message.channel.send("아직 포켓몬을 잡은 사람이 아무도 없습니다!")
            return
        
        ranking.sort(key=lambda x: x[1], reverse=True)
        embed = discord.Embed(title="🏆 포켓몬 헌터 순위 TOP 5", color=0xffd700)
        
        for i, (user_id, count) in enumerate(ranking[:5]):
            discord_user = client.get_user(int(user_id))
            name = discord_user.display_name if discord_user else "알 수 없는 헌터"
            medals = ["🥇", "🥈", "🥉", "🏅", "🏅"]
            medal = medals[i]
            embed.add_field(name=f"{medal} {i+1}위: {name}", value=f"총 {count}마리 포획", inline=False)
            
        await message.channel.send(embed=embed)

keep_alive()
client.run(os.environ.get('DISCORD_TOKEN'))

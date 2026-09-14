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

# ==========================================
# 3. 픽셀몬 위키 스크래핑 정밀화 및 텍스트 정제 함수
# ==========================================
async def get_pixelmon_spawn_info(english_name):
    formatted_name = english_name.capitalize()
    wiki_url = f"https://pixelmonmod.com/wiki/{formatted_name}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(wiki_url, headers=headers, timeout=4) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    biomes = set()
                    times = set()

                    filter_words = {
                        'biome', 'biomes', 'modid', 'time', 'times', 'location', 
                        'condition', 'rarity', 'spawn time', 'level range in wild', 
                        'level', 'range', 'in', 'wild', ''
                    }

                    tables = soup.find_all('table')
                    for table in tables:
                        rows = table.find_all('tr')
                        for row in rows:
                            text_nodes = [td.get_text().strip() for td in row.find_all(['td', 'th'])]
                            header_line = " ".join(text_nodes).lower()

                            if 'biome' in header_line:
                                for td in row.find_all('td'):
                                    val = td.get_text().strip()
                                    if val and val.lower() not in filter_words and not val.replace(' ', '').isdigit():
                                        clean_val = re.sub(r'\s+', ' ', val)
                                        if clean_val.lower() not in filter_words:
                                            biomes.add(clean_val)

                            if 'time' in header_line:
                                for td in row.find_all('td'):
                                    val = td.get_text().strip()
                                    for t in ['Day', 'Night', 'Dawn', 'Dusk', 'Any']:
                                        if t.lower() in val.lower():
                                            times.add(t)

                    final_biome = ", ".join(sorted(list(biomes))) if biomes else "Plains, Forest (기본)"
                    final_time = ", ".join(sorted(list(times))) if times else "Day (낮)"

                    clean_biomes = [b for b in final_biome.split(', ') if b.lower() not in filter_words]
                    final_biome = ", ".join(clean_biomes) if clean_biomes else "Plains, Forest (기본)"

                    if len(final_biome) > 60:
                        final_biome = final_biome[:57] + "..."

                    return final_biome, final_time
    except Exception:
        pass

    return "Mountain, Hills (기본)", "Day (낮)"

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
    height = data['height'] / 10
    weight = data['weight'] / 10
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
        "

import discord
import os
import requests
import aiohttp
import asyncio
import re
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
    t = Thread(target=run_server)
    t.start()

# ==========================================
# 2. 디스코드 봇 설정
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

POKEMON_KO_TO_ID = {}

TYPE_KO = {
    'normal': '⚪ 노말', 'fire': '🔥 불꽃', 'water': '💧 물',
    'grass': '🌿 풀', 'electric': '⚡ 전기', 'ice': '❄️ 얼음',
    'fighting': '🥊 격투', 'poison': '☠️ 독', 'ground': '⏳ 땅',
    'flying': '🕊️ 비행', 'psychic': '🔮 에스퍼', 'bug': '🐛 벌레',
    'rock': '🪨 바위', 'ghost': '👻 고스트', 'dragon': '🐉 드래곤',
    'steel': '⚙️ 강철', 'fairy': '✨ 페어리', 'dark': '🌙 악'
}

ABILITY_KO_CACHE = {}

EGG_GROUP_KO = {
    'monster': '괴수', 'water1': '수중 1', 'water2': '수중 2', 'water3': '수중 3',
    'bug': '벌레', 'flying': '비행', 'field': '육상', 'ground': '육상',
    'fairy': '요정', 'grass': '식물', 'human-like': '인간형', 'mineral': '광물',
    'amorphous': '부정형', 'dragon': '드래곤', 'undiscovered': '미발견',
}

# --- 폼 체인지 번역 및 이모지 사전 ---
FORM_TRANSLATIONS = {
    'mega-x': ('메가진화 X', '✨', discord.ButtonStyle.success),
    'mega-y': ('메가진화 Y', '✨', discord.ButtonStyle.success),
    'mega': ('메가진화', '✨', discord.ButtonStyle.success),
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

# ==========================================
# 3. 특성 및 알 그룹 정보 로드
# ==========================================
async def get_ability_korean_name(session, ability_name):
    key = ability_name.lower()
    if key in ABILITY_KO_CACHE: return ABILITY_KO_CACHE[key]
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
    except Exception: pass
    fallback = ability_name.replace('-', ' ').title()
    ABILITY_KO_CACHE[key] = fallback
    return fallback

async def get_pokemon_extra_info(data, species_data):
    normal_abilities, hidden_abilities = [], []
    async with aiohttp.ClientSession() as session:
        for ability_entry in data.get('abilities', []):
            ability_name = ability_entry.get('ability', {}).get('name')
            is_hidden = ability_entry.get('is_hidden', False)
            if ability_name:
                korean_name = await get_ability_korean_name(session, ability_name)
                if is_hidden:
                    if korean_name not in hidden_abilities: hidden_abilities.append(korean_name)
                else:
                    if korean_name not in normal_abilities: normal_abilities.append(korean_name)

    normal_text = ", ".join(normal_abilities) if normal_abilities else "정보 없음"
    hidden_text = ", ".join(hidden_abilities) if hidden_abilities else "없음"
    ability_text = f"{normal_text}\n(숨겨진: {hidden_text})" if hidden_text != "없음" else normal_text

    egg_groups = []
    for group in species_data.get('egg_groups', []):
        group_name = group.get('name', '')
        korean_name = EGG_GROUP_KO.get(group_name, group_name.replace('-', ' ').title())
        if korean_name and korean_name not in egg_groups: egg_groups.append(korean_name)
    egg_group_text = ", ".join(egg_groups) if egg_groups else "미발견"

    hatch_counter = species_data.get('hatch_counter')
    hatch_text = f"{hatch_counter * 255:,}" if hatch_counter is not None else "정보 없음"
    capture_rate = species_data.get('capture_rate')
    catch_text = str(capture_rate) if capture_rate is not None else "정보 없음"

    return ability_text, egg_group_text, hatch_text, catch_text

# ==========================================
# 4. 픽셀몬 위키 스폰 정보 (바이옴 및 시간)
# ==========================================
PIXELMON_SPAWN_CACHE = {}
PIXELMON_HEADERS = {"User-Agent": "Mozilla/5.0"}
TIME_ORDER = ["Dawn", "Morning", "Day", "Midday", "Afternoon", "Dusk", "Night", "Midnight", "Any"]

def normalize_wiki_name(name): return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()

def clean_biome_text(text):
    if not text: return []
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*\(Forge\)", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*\(Mod\)", "", text, flags=re.IGNORECASE).strip()
    low = text.lower()
    
    exclude_tags = ["evil", "magical", "spooky", "dead", "wasteland", "void"]
    if low in exclude_tags: return []

    category_map = {
        "forests": ["Forest", "Birch Forest", "Dark Forest", "Flower Forest"],
        "plains": ["Plains", "Sunflower Plains", "Meadow"],
        "taigas": ["Taiga", "Snowy Taiga", "Old Growth Pine Taiga"],
        "jungles": ["Jungle", "Sparse Jungle", "Bamboo Jungle"],
        "savannas": ["Savanna", "Windswept Savanna"],
        "deserts": ["Desert"],
        "swamps": ["Swamp", "Mangrove Swamp"],
        "oceans": ["Ocean", "Deep Ocean", "Warm Ocean", "Cold Ocean"],
        "beaches": ["Beach", "Snowy Beach"],
        "mountains": ["Meadow", "Grove", "Snowy Slopes", "Jagged Peaks"]
    }
    for k in category_map:
        if low == k or low == f"{k} (category)": return category_map[k]

    invalid_keywords = ["voice", "endure", "ratio", "gender", "level", "wild", "tm", "tutor", "egg"]
    if any(bad in low for bad in invalid_keywords): return []
    return [text]

def extract_times(text):
    found = []
    text = text or ""
    for tk in TIME_ORDER:
        if re.search(rf"\b{re.escape(tk)}\b", text, re.IGNORECASE): found.append(tk)
    return found if found else []

def parse_normal_spawn_table(soup):
    biomes, times = set(), set()
    heading = None
    for tag in soup.find_all(["h2", "h3"]):
        if "spawn biomes" in tag.get_text(" ", strip=True).lower():
            heading = tag
            break
    if not heading: return biomes, times
    table = heading.find_next("table")
    if not table: return biomes, times

    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 3: continue
        values = [cell.get_text(" ", strip=True) for cell in cells]
        if values[0].strip().lower() == "biome": continue
        biomes.update(clean_biome_text(values[0]))
        if len(values) > 2: times.update(extract_times(values[2]))
    return biomes, times

async def parse_legendary_spawn_table(session, english_name):
    url = "https://pixelmonmod.com/wiki/Legendary_Pokemon"
    target = normalize_wiki_name(english_name)
    try:
        async with session.get(url, headers=PIXELMON_HEADERS, timeout=12) as response:
            if response.status != 200: return set(), set()
            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            biomes, times = set(), set()
            for table in soup.find_all("table"):
                for row in table.find_all("tr"):
                    cells = row.find_all(["th", "td"], recursive=False)
                    if len(cells) < 4: continue
                    values = [cell.get_text(" ", strip=True) for cell in cells]
                    if normalize_wiki_name(values[0]) == target:
                        biomes.update(clean_biome_text(values[1]))
                        times.update(extract_times(values[3]))
                        return biomes, times
    except: pass
    return set(), set()

async def get_pixelmon_spawn_info(english_name):
    cache_key = normalize_wiki_name(english_name)
    if cache_key in PIXELMON_SPAWN_CACHE: return PIXELMON_SPAWN_CACHE[cache_key]
    
    wiki_url = f"https://pixelmonmod.com/wiki/{english_name.replace(' ', '_').capitalize()}"
    try:
        async with aiohttp.ClientSession() as session:
            biomes, times = set(), set()
            async with session.get(wiki_url, headers=PIXELMON_HEADERS, timeout=12) as response:
                if response.status == 200:
                    html = await response.text()
                    biomes, times = parse_normal_spawn_table(BeautifulSoup(html, "html.parser"))
            
            if not biomes:
                l_biomes, l_times = await parse_legendary_spawn_table(session, english_name)
                biomes.update(l_biomes)
                times.update(l_times)

            sorted_times = [t for t in TIME_ORDER if t in times]
            time_text = ", ".join(sorted_times) if sorted_times else "Any"
            biome_text = ", ".join(sorted(biomes, key=str.lower)) if biomes else "정보 없음"
            if len(biome_text) > 1000: biome_text = biome_text[:997] + "..."
            
            PIXELMON_SPAWN_CACHE[cache_key] = (biome_text, time_text)
            return (biome_text, time_text)
    except:
        return ("정보 없음", "Any")

# ==========================================
# 5. 한국어 사전 구축 및 ID 변환 (문지기 해제 완료)
# ==========================================
async def fetch_species_name(session, item):
    try:
        async with session.get(item['url']) as response:
            if response.status == 200:
                data = await response.json()
                for n in data.get('names', []):
                    if n['language']['name'] == 'ko':
                        POKEMON_KO_TO_ID[n['name'].lower()] = str(data['id'])
                        break
    except: pass

async def build_korean_cache():
    print('🔄 포켓몬 한국어 데이터베이스 수집 중...')
    async with aiohttp.ClientSession() as session:
        async with session.get('https://pokeapi.co/api/v2/pokemon-species?limit=1025') as response:
            if response.status == 200:
                tasks = [fetch_species_name(session, item) for item in (await response.json())['results']]
                await asyncio.gather(*tasks)
    print("✅ 데이터베이스 수집 완료!")

def resolve_pokemon_id(query):
    query = query.strip().lower()
    if query.isdigit(): return query
    if query in POKEMON_KO_TO_ID: return POKEMON_KO_TO_ID[query]
    
    # 순수 영문(버튼에서 넘겨준 폼 데이터)이면 무조건 통과!
    if not re.search(r'[가-힣]', query):
        return query
        
    try:
        res = requests.get(f'https://pokeapi.co/api/v2/pokemon-species/{query}')
        if res.status_code == 200: return str(res.json()['id'])
    except: pass
    
    return None

# ==========================================
# 6. Embed 생성기 (조회 버그 수정 완료)
# ==========================================
async def get_pokemon_embed_async(pokemon_id_or_name):
    query = str(pokemon_id_or_name).strip().lower()
    pokemon_id = resolve_pokemon_id(query)
    if not pokemon_id: return None, None

    # 상세 정보(폼 데이터)부터 다이렉트로 가져옵니다
    detail_url = f'https://pokeapi.co/api/v2/pokemon/{query}'
    detail_res = requests.get(detail_url)
    if detail_res.status_code != 200:
        detail_url = f'https://pokeapi.co/api/v2/pokemon/{pokemon_id}'
        detail_res = requests.get(detail_url)

    if detail_res.status_code != 200: return None, None
    data = detail_res.json()

    # 상세 데이터 안의 종족(Species) 원본 주소를 사용
    species_url = data['species']['url']
    species_res = requests.get(species_url)
    if species_res.status_code != 200: return None, None
    species_data = species_res.json()

    display_name = str(species_data['id'])
    for name_entry in species_data['names']:
        if name_entry['language']['name'] == 'ko':
            display_name = name_entry['name']
            break

    image_url = data['sprites']['other']['official-artwork']['front_default'] or data['sprites']['front_default']

    # 폼 이름 예쁘게 포맷팅 (번역 사전 연동 자동화)
    form_applied = False
    for key, (k_name, _, _) in FORM_TRANSLATIONS.items():
        if key in query:
            if key in ['mega-x', 'mega-y', 'mega', 'alola', 'galar', 'hisui', 'paldea', 'primal', 'black', 'white', 'ultra']:
                display_name = f"{k_name.replace(' 폼', '').replace('진화', '')} {display_name}"
            else:
                display_name = f"{display_name} ({k_name})"
            form_applied = True
            break
            
    # 사전에 없는 마이너 폼은 영어 단어를 추출해 괄호로 자동 처리
    if not form_applied and '-' in query:
        base_eng = species_data['name']
        form_suffix = query.replace(f"{base_eng}-", "").replace("-", " ")
        if form_suffix != query:
            display_name = f"{display_name} ({form_suffix.title()} 폼)"

    types_list = [TYPE_KO.get(t['type']['name'], t['type']['name'].capitalize()) for t in data['types']]
    types_str = ", ".join(types_list)

    stats = {stat['stat']['name']: stat['base_stat'] for stat in data['stats']}
    total = sum(stats.values())
    stats_table = (
        "```text\n+-----------------------------------+\n"
        "|  HP  Atk  Def  SpA  SpD  Spe | Total |\n+-----------------------------------+\n"
        f"| {stats.get('hp',0):>3}  {stats.get('attack',0):>3}  {stats.get('defense',0):>3}  {stats.get('special-attack',0):>3}  {stats.get('special-defense',0):>3}  {stats.get('speed',0):>3} |  {total:>3}  |\n"
        "+-----------------------------------+```"
    )

    biome_info, time_info = await get_pixelmon_spawn_info(species_data['name'])
    ability_text, egg_group_text, hatch_text, catch_text = await get_pokemon_extra_info(data, species_data)

    rarity_badge, rarity_color = "", 0x3498db
    if species_data.get("is_legendary", False):
        rarity_badge, rarity_color = "🌟 전설", 0xF1C40F
    elif species_data.get("is_mythical", False):
        rarity_badge, rarity_color = "✨ 환상", 0x9B59B6

    title_prefix = f"{rarity_badge} " if rarity_badge else ""

    embed = discord.Embed(title=f"{title_prefix}포켓몬 정보: {display_name}", color=rarity_color)
    embed.set_thumbnail(url=image_url)
    embed.add_field(name="📖 도감 번호", value=f"No.{species_data['id']}", inline=False)
    embed.add_field(name="⚔️ 타입", value=types_str, inline=True)
    embed.add_field(name="🛡️ 특성", value=ability_text, inline=True)
    embed.add_field(name="🥚 알 그룹", value=egg_group_text, inline=True)
    embed.add_field(name="🍄 출몰 바이옴", value=biome_info, inline=True)
    embed.add_field(name="⏳ 출몰 시간", value=time_info, inline=True)
    embed.add_field(name="🌸 종족치", value=stats_table, inline=False)

    return embed, species_data

# ==========================================
# 7. 진화 및 폼 체인지 통합 버튼 뷰
# ==========================================
class PokemonEvolutionView(discord.ui.View):
    def __init__(self, species_data, current_pokemon_name, base_species_name=None):
        super().__init__(timeout=120)
        self.species_data = species_data
        self.current_name = current_pokemon_name
        self.base_species_name = base_species_name or species_data['name']
        self.setup_buttons()

    def setup_buttons(self):
        added_custom_ids = set()

        # 1. 원래 형태 돌아가기
        if self.current_name != self.base_species_name:
            cid = f"evo_{self.base_species_name}"
            if cid not in added_custom_ids:
                btn = discord.ui.Button(label="🔄 원래 형태", style=discord.ButtonStyle.secondary, custom_id=cid)
                btn.callback = self.on_evolution_click
                self.add_item(btn)
                added_custom_ids.add(cid)

        # 2. 진화 버튼 생성
        try:
            evo_chain_url = self.species_data['evolution_chain']['url']
            chain_res = requests.get(evo_chain_url)
            if chain_res.status_code == 200:
                chain_data = chain_res.json()['chain']
                prev_evo, next_evos = self.find_evolutions(chain_data, self.species_data['name'])

                if prev_evo and prev_evo != self.current_name:
                    cid = f"evo_{prev_evo}"
                    if cid not in added_custom_ids:
                        btn = discord.ui.Button(label="이전 진화", style=discord.ButtonStyle.secondary, custom_id=cid)
                        btn.callback = self.on_evolution_click
                        self.add_item(btn)
                        added_custom_ids.add(cid)

                for next_evo in next_evos:
                    if next_evo != self.current_name:
                        cid = f"evo_{next_evo}"
                        if cid not in added_custom_ids:
                            btn = discord.ui.Button(label=f"진화 ({next_evo.capitalize()})", style=discord.ButtonStyle.primary, custom_id=cid)
                            btn.callback = self.on_evolution_click
                            self.add_item(btn)
                            added_custom_ids.add(cid)
        except Exception:
            pass

        # 3. 폼 체인지 버튼 자동 매핑 (사전 + 자동 생성)
        varieties = self.species_data.get('varieties', [])
        for var in varieties:
            var_name = var['pokemon']['name']
            
            if var_name == self.current_name or var_name == self.base_species_name:
                continue

            cid = f"evo_{var_name}"
            if cid in added_custom_ids:
                continue

            label_name, emoji, style = None, "🔄", discord.ButtonStyle.secondary

            # 사전(FORM_TRANSLATIONS)에서 키워드 확인 및 적용
            for key, (k_name, k_emoji, k_style) in FORM_TRANSLATIONS.items():
                if key in var_name:
                    label_name, emoji, style = k_name, k_emoji, k_style
                    break
            
            # 사전에 없는 마이너 폼은 자동으로 이름 생성
            if not label_name:
                form_suffix = var_name.replace(f"{self.base_species_name}-", "").replace("-", " ")
                label_name = f"{form_suffix.title()} 폼"

            btn = discord.ui.Button(label=f"{emoji} {label_name}", style=style, custom_id=cid)
            btn.callback = self.on_evolution_click
            self.add_item(btn)
            added_custom_ids.add(cid)

    def find_evolutions(self, chain_node, target_name):
        if chain_node['species']['name'] == target_name:
            return None, [child['species']['name'] for child in chain_node['evolves_to']]
        for child in chain_node['evolves_to']:
            if child['species']['name'] == target_name:
                return child['species']['name'], [gc['species']['name'] for gc in child['evolves_to']]
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
            await interaction.followup.send("폼 체인지 정보를 불러올 수 없습니다.", ephemeral=True)

# ==========================================
# 8. 디스코드 이벤트 핸들러 및 실행
# ==========================================
@client.event
async def on_ready():
    await build_korean_cache()
    print('🚀 도감 검색 전용 포켓몬 봇 가동 완료!')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith('!도감 '):
        query = message.content.split(' ')[1].strip()
        embed, species_data = await get_pokemon_embed_async(query)

        if embed and species_data:
            base_name = species_data['name']
            view = PokemonEvolutionView(species_data, base_name, base_name)
            try:
                await message.channel.send(embed=embed, view=view)
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(2)
                    await message.channel.send(embed=embed, view=view)
                else:
                    raise e
        else:
            await message.channel.send('포켓몬을 찾을 수 없어요. 포켓몬 이름이나 번호를 정확히 입력해 주세요!')

keep_alive()
client.run(os.environ.get('DISCORD_TOKEN'))

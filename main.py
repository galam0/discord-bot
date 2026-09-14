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
# 3. 픽셀몬 위키 스폰 전용 테이블 정밀 추출 함수
# ==========================================
async def get_pixelmon_spawn_info(english_name):
    """
    Pixelmon Wiki의 'Spawn Biomes' 표에서만 출몰 시간과 바이옴을 가져옵니다.
    페이지 전체의 다른 표(Moves, Held item 등)는 절대 스캔하지 않습니다.
    """
    formatted_name = english_name.capitalize()
    wiki_url = f"https://pixelmonmod.com/wiki/{formatted_name}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    # Pixelmon의 실제 Spawn Time 종류
    time_order = [
        "Dawn", "Morning", "Day", "Midday",
        "Afternoon", "Dusk", "Night", "Midnight", "Any"
    ]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                wiki_url,
                headers=headers,
                timeout=10
            ) as response:

                if response.status != 200:
                    print(f"❌ 위키 접속 실패: {english_name} / HTTP {response.status}")
                    return "정보 없음", "Any"

                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")

                # ==========================================
                # 1. 'Spawn Biomes' 섹션 제목을 정확히 찾기
                # ==========================================
                spawn_heading = None

                for heading in soup.find_all(["h2", "h3", "h4"]):
                    heading_text = heading.get_text(" ", strip=True).lower()

                    if "spawn biomes" in heading_text:
                        spawn_heading = heading
                        break

                if spawn_heading is None:
                    print(f"⚠️ Spawn Biomes 섹션을 찾지 못함: {english_name}")
                    return "정보 없음", "Any"

                # ==========================================
                # 2. Spawn Biomes 제목 바로 아래의 표만 사용
                # ==========================================
                spawn_table = spawn_heading.find_next("table")

                if spawn_table is None:
                    print(f"⚠️ Spawn Biomes 표를 찾지 못함: {english_name}")
                    return "정보 없음", "Any"

                biomes = set()
                times = set()

                # ==========================================
                # 3. 표의 행을 분석
                #
                # 공식 표 구조:
                # Biome | ModID | Time | Location | Condition | Rarity
                # ==========================================
                for row in spawn_table.find_all("tr"):
                    cells = row.find_all(["th", "td"])

                    if len(cells) < 3:
                        continue

                    values = [
                        cell.get_text(" ", strip=True)
                        for cell in cells
                    ]

                    # 헤더 행 무시
                    if values[0].strip().lower() == "biome":
                        continue

                    biome = values[0].strip()
                    spawn_time = values[2].strip()

                    # 바이옴 저장
                    if biome:
                        biomes.add(biome)

                    # 출몰 시간 저장
                    if spawn_time:
                        # 여러 시간이 한 셀에 들어오는 경우도 대비
                        for time_name in time_order:
                            if re.search(
                                rf"\b{re.escape(time_name)}\b",
                                spawn_time,
                                re.IGNORECASE
                            ):
                                times.add(time_name)

                # ==========================================
                # 4. 표시 순서 정리
                # ==========================================
                sorted_times = [
                    time_name
                    for time_name in time_order
                    if time_name in times
                ]

                # 바이옴은 알파벳순으로 정렬
                sorted_biomes = sorted(biomes, key=str.lower)

                time_text = ", ".join(sorted_times) if sorted_times else "Any"
                biome_text = ", ".join(sorted_biomes) if sorted_biomes else "정보 없음"

                # ==========================================
                # 5. Discord Embed field 길이 보호
                # ==========================================
                if len(time_text) > 1000:
                    time_text = time_text[:997] + "..."

                if len(biome_text) > 1000:
                    biome_text = biome_text[:997] + "..."

                print(
                    f"✅ {english_name}: "
                    f"시간=[{time_text}] / 바이옴=[{biome_text}]"
                )

                return biome_text, time_text

    except Exception as e:
        print(f"❌ Pixelmon 스폰 정보 오류: {english_name} / {e}")

    return "정보 없음", "Any"

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
        "```"
    )

    biome_info, time_info = await get_pixelmon_spawn_info(english_name)

    embed = discord.Embed(title=f"포켓몬 정보: {display_name}", color=0xcc0000)
    embed.set_thumbnail(url=image_url)
    embed.add_field(name="도감 번호", value=f"No.{species_data['id']}", inline=False)
    embed.add_field(name="타입", value=types_str, inline=False)
    embed.add_field(name="키", value=f"{height}m", inline=True)
    embed.add_field(name="몸무게", value=f"{weight}kg", inline=True)
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

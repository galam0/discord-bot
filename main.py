import discord
import os
import requests
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

TYPE_KO = {
    'normal': '⚪ 노말', 'fire': '🔥 불꽃', 'water': '💧 물',
    'grass': '🌿 풀', 'electric': '⚡ 전기', 'ice': '❄️ 얼음',
    'fighting': '🥊 격투', 'poison': '☠️ 독', 'ground': '⏳ 땅',
    'flying': '🕊️ 비행', 'psychic': '🔮 에스퍼', 'bug': '🐛 벌레',
    'rock': '🪨 바위', 'ghost': '👻 고스트', 'dragon': '🐉 드래곤',
    'steel': '⚙️ 강철', 'fairy': '✨ 페어리', 'dark': '🌙 악'
}

# ==========================================
# 3. 효율화된 도감 검색 함수 (서버 멈춤 방지)
# ==========================================
def get_pokemon_embed(pokemon_id_or_name):
    query = str(pokemon_id_or_name).strip().lower()

    # 1. Species 데이터 가져오기 (1회만 요청)
    species_url = f'https://pokeapi.co/api/v2/pokemon-species/{query}'
    species_res = requests.get(species_url)

    if species_res.status_code != 200:
        return None, None

    species_data = species_res.json()
    pokemon_id = species_data['id']

    # 한국어 공식 이름 추출
    display_name = str(pokemon_id)
    for name_entry in species_data['names']:
        if name_entry['language']['name'] == 'ko':
            display_name = name_entry['name']
            break

    # 2. 상세 정보 가져오기 (타입, 이미지 등)
    detail_url = f'https://pokeapi.co/api/v2/pokemon/{pokemon_id}'
    detail_res = requests.get(detail_url)
    if detail_res.status_code != 200:
        return None, None
        
    data = detail_res.json()
    height = data['height'] / 10
    weight = data['weight'] / 10
    image_url = data['sprites']['other']['official-artwork']['front_default'] or data['sprites']['front_default']

    if 'mega' in str(pokemon_id_or_name).lower():
        display_name = f"메가 {display_name}"

    types_list = [TYPE_KO.get(t['type']['name'], t['type']['name'].capitalize()) for t in data['types']]
    types_str = ", ".join(types_list)

    embed = discord.Embed(title=f"포켓몬 정보: {display_name}", color=0xcc0000)
    embed.set_thumbnail(url=image_url)
    embed.add_field(name="도감 번호", value=f"No.{pokemon_id}", inline=False)
    embed.add_field(name="타입", value=types_str, inline=False)
    embed.add_field(name="키", value=f"{height}m", inline=True)
    embed.add_field(name="몸무게", value=f"{weight}kg", inline=True)

    return embed, species_data

# ==========================================
# 4. 진화 / 메가진화 버튼 View 클래스 (defer 처리)
# ==========================================
class PokemonEvolutionView(discord.ui.View):
    def __init__(self, species_data, current_pokemon_name):
        super().__init__(timeout=120)
        self.species_data = species_data
        self.current_name = current_pokemon_name
        self.setup_buttons()

    def setup_buttons(self):
        evo_chain_url = self.species_data['evolution_chain']['url']
        chain_res = requests.get(evo_chain_url)
        
        if chain_res.status_code == 200:
            chain_data = chain_res.json()['chain']
            prev_evo, next_evos = self.find_evolutions(chain_data, self.species_data['name'])

            if prev_evo:
                btn = discord.ui.Button(label="이전 진화", style=discord.ButtonStyle.secondary, custom_id=f"evo_{prev_evo}")
                btn.callback = self.on_evolution_click
                self.add_item(btn)

            for next_evo in next_evos:
                btn = discord.ui.Button(label=f"진화 ({next_evo.capitalize()})", style=discord.ButtonStyle.primary, custom_id=f"evo_{next_evo}")
                btn.callback = self.on_evolution_click
                self.add_item(btn)

        varieties = self.species_data.get('varieties', [])
        for var in varieties:
            var_name = var['pokemon']['name']
            if 'mega' in var_name:
                label_name = "메가진화"
                if 'mega-x' in var_name: label_name = "메가진화 X"
                elif 'mega-y' in var_name: label_name = "메가진화 Y"

                btn = discord.ui.Button(label=f"✨ {label_name}", style=discord.ButtonStyle.success, custom_id=f"evo_{var_name}")
                btn.callback = self.on_evolution_click
                self.add_item(btn)

    def find_evolutions(self, chain_node, target_name):
        if chain_node['species']['name'] == target_name:
            next_evos = [child['species']['name'] for child in chain_node['evolves_to']]
            return None, next_evos

        for child in chain_node['evolves_to']:
            if child['species']['name'] == target_name:
                next_evos = [grandchild['species']['name'] for grandchild in child['evolves_to']]
                return chain_node['species']['name'], next_evos
            
            for grandchild in child['evolves_to']:
                if grandchild['species']['name'] == target_name:
                    return child['species']['name'], []
        return None, []

    async def on_evolution_click(self, interaction: discord.Interaction):
        # 3초 타임아웃 방지
        await interaction.response.defer()

        target_pokemon = interaction.data['custom_id'].replace('evo_', '')
        embed, new_species_data = get_pokemon_embed(target_pokemon)

        if embed and new_species_data:
            new_view = PokemonEvolutionView(new_species_data, target_pokemon)
            await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=new_view)
        else:
            await interaction.followup.send("진화 정보를 불러올 수 없습니다.", ephemeral=True)

# ==========================================
# 5. 디스코드 이벤트 핸들러 및 명령어
# ==========================================
@client.event
async def on_ready():
    print('포켓몬 봇이 성공적으로 실행되었습니다!')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # 1. 도감 사용법
    if message.content == '!도감':
        embed = discord.Embed(
            title="📖 포켓몬 도감 사용법",
            description="`!도감 [포켓몬 번호 또는 영어이름]` 형식으로 입력해 주세요!\n\n**예시:**\n• `!도감 6` (리자몽 - 진화/메가진화 테스트)\n• `!도감 25` 또는 `!도감 pikachu` (피카츄)",
            color=0x3498db
        )
        await message.channel.send(embed=embed)

    # 2. 도감 상세 검색
    elif message.content.startswith('!도감 '):
        query = message.content.split(' ')[1].strip()
        embed, species_data = get_pokemon_embed(query)

        if embed and species_data:
            view = PokemonEvolutionView(species_data, species_data['name'])
            await message.channel.send(embed=embed, view=view)
        else:
            await message.channel.send('포켓몬을 찾을 수 없어요. 도감 번호(예: !도감 6)나 영어 이름(예: !도감 charizard)으로 검색해 주세요!')

    # 3. 포획 기능
    elif message.content.startswith('!포획 '):
        query = message.content.split(' ')[1].strip()
        embed, species_data = get_pokemon_embed(query)

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
            await message.channel.send("존재하지 않는 포켓몬입니다. 포켓몬 번호나 영어 이름을 정확히 입력해 주세요!")

    # 4. 내 포켓몬 목록
    elif message.content == '!내포켓몬':
        user_id = str(message.author.id)
        user_data = collection.find_one({"_id": user_id})
        
        if user_data and len(user_data["pokemon_list"]) > 0:
            pokemon_list = ", ".join(user_data["pokemon_list"])
            await message.channel.send(f"📖 **{message.author.display_name}님의 포켓몬 도감**\n{pokemon_list}")
        else:
            await message.channel.send("아직 잡은 포켓몬이 없습니다.")

    # 5. 순위 표기
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

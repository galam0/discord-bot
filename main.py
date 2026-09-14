import discord
import os
import requests
from pymongo import MongoClient
from flask import Flask
from threading import Thread

# 1. 24시간 유지를 위한 가짜 웹 서버 설정
app = Flask(__name__)

@app.route('/')
def home():
    return "포켓몬 봇이 24시간 온라인 상태입니다!"

def run_server():
    # Render가 요구하는 웹 포트 설정
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_server)
    t.start()

# 2. 기존 디스코드 봇 및 DB 설정
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

pokemon_ko = {
    "이상해씨": 1, "파이리": 4, "꼬부기": 7, "피카츄": 25, 
    "라이츄": 26, "잠만보": 143, "뮤츠": 150, "뮤": 151
}

mongo_uri = os.environ.get('MONGODB_URI')
mongo_client = MongoClient(mongo_uri)
db = mongo_client['pokemon_game']
collection = db['users']

@client.event
async def on_ready():
    print('웹 서비스가 연결된 포켓몬 봇이 켜졌습니다!')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith('!도감 '):
        search_query = message.content.split(' ')[1]
        display_name = search_query 

        if search_query in pokemon_ko:
            search_query = str(pokemon_ko[search_query])
        else:
            display_name = search_query.capitalize()

        url = f'https://pokeapi.co/api/v2/pokemon/{search_query.lower()}'
        response = requests.get(url)
        
        if response.status_code == 200:
            data = response.json()
            height = data['height'] / 10
            weight = data['weight'] / 10
            image_url = data['sprites']['front_default']
            
            embed = discord.Embed(title=f"포켓몬 정보: {display_name}", color=0xcc0000)
            embed.set_thumbnail(url=image_url)
            embed.add_field(name="도감 번호", value=f"No.{data['id']}", inline=False)
            embed.add_field(name="키", value=f"{height}m", inline=True)
            embed.add_field(name="몸무게", value=f"{weight}kg", inline=True)
            
            await message.channel.send(embed=embed)
        else:
            await message.channel.send('포켓몬을 찾을 수 없어요.')

    if message.content.startswith('!포획 '):
        pokemon_name = message.content.split(' ')[1]
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
                await message.channel.send("이미 도감에 등록된 포켓몬입니다.")

    if message.content == '!내포켓몬':
        user_id = str(message.author.id)
        user_data = collection.find_one({"_id": user_id})
        
        if user_data and len(user_data["pokemon_list"]) > 0:
            pokemon_list = ", ".join(user_data["pokemon_list"])
            await message.channel.send(f"📖 **{message.author.display_name}님의 포켓몬 도감**\n{pokemon_list}")
        else:
            await message.channel.send("아직 잡은 포켓몬이 없습니다.")

    if message.content == '!순위':
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

# 3. 봇을 켜기 전에 웹 서버(가짜 웹사이트)를 먼저 실행합니다.
keep_alive()
client.run(os.environ.get('DISCORD_TOKEN'))

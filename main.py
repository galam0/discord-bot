import os
import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask
from threading import Thread
import requests

# 봇 설정
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 가짜 웹 서버 설정 (Render 유지용)
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ----------------------------------------------------
# [발로란트 API 연동 로직]
# ----------------------------------------------------
def get_valorant_shop(username, password, region="kr"):
    session = requests.Session()
    headers = {'User-Agent': 'RiotClient/43.0.1.4195386.4190634 rso-auth (Windows;10;10.0.19041.1.256.64bit)'}
    
    # 1. 라이엇 RSO 로그인 요청
    auth_body = {
        "client_id": "play-valorant-web-prod",
        "nonce": "1",
        "redirect_uri": "https://playvalorant.com/opt_in",
        "response_type": "token id_token",
    }
    session.post('https://auth.riotgames.com/api/v1/authorization', json=auth_body, headers=headers)
    
    login_body = {
        "type": "auth",
        "username": username,
        "password": password,
        "remember": "true"
    }
    
    login_res = session.put('https://auth.riotgames.com/api/v1/authorization', json=login_body, headers=headers).json()
    
    if 'error' in login_res:
        return None, "❌ 로그인 실패: 아이디/비밀번호가 틀렸거나 2단계 인증(2FA)이 설정된 계정입니다."

    # access_token 및 entitlements_token 추출
    try:
        uri = login_res['response']['parameters']['uri']
        access_token = uri.split('access_token=')[1].split('&')[0]

        ent_res = session.post(
            'https://entitlements.auth.riotgames.com/api/token/v1', 
            headers={'Authorization': f'Bearer {access_token}'}
        ).json()
        entitlements_token = ent_res['entitlements_token']

        user_res = session.post(
            'https://auth.riotgames.com/userinfo', 
            headers={'Authorization': f'Bearer {access_token}'}
        ).json()
        puuid = user_res['sub']

        # 상점 데이터 가져오기
        shop_headers = {
            'Authorization': f'Bearer {access_token}',
            'X-Riot-Entitlements-JWT': entitlements_token,
        }
        shop_url = f'https://pd.{region}.a.pvp.net/store/v2/storefront/{puuid}'
        shop_data = session.get(shop_url, headers=shop_headers).json()

        single_item_offers = shop_data['SkinsPanelLayout']['SingleItemOffers']
        
        # valorant-api.com 통해 스킨 상세정보 매핑
        skins_info = []
        for skin_id in single_item_offers:
            skin_res = requests.get(f'https://valorant-api.com/v1/weapons/skinlevels/{skin_id}?language=ko-KR').json()
            if skin_res['status'] == 200:
                skins_info.append({
                    'name': skin_res['data']['displayName'],
                    'icon': skin_res['data']['displayIcon']
                })

        return skins_info, None
    except Exception as e:
        return None, f"❌ 상점 정보 처리 중 오류가 발생했습니다: {e}"


# ----------------------------------------------------
# [보안 입력 모달 창 구현]
# ----------------------------------------------------
class ValorantAuthModal(discord.ui.Modal, title="발로란트 로그인"):
    username = discord.ui.TextInput(
        label="라이엇 계정 ID",
        placeholder="아이디를 입력하세요",
        required=True
    )
    password = discord.ui.TextInput(
        label="비밀번호",
        placeholder="비밀번호를 입력하세요",
        style=discord.TextStyle.short,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        # API 조회 시간이 소요되므로 일시 대기 반응
        await interaction.response.defer(ephemeral=True)
        
        skins, error = get_valorant_shop(self.username.value, self.password.value)
        
        if error:
            await interaction.followup.send(content=error, ephemeral=True)
            return

        embed = discord.Embed(
            title="🛒 오늘의 발로란트 일일 상점", 
            color=discord.Color.red(),
            description="본인에게만 보이는 상점 정보입니다."
        )
        
        for idx, skin in enumerate(skins, 1):
            embed.add_field(name=f"{idx}. {skin['name']}", value="\u200b", inline=False)

        # 4번째 스킨 이미지를 대표 썸네일로 설정
        if skins and skins[-1]['icon']:
            embed.set_thumbnail(url=skins[-1]['icon'])

        await interaction.followup.send(embed=embed, ephemeral=True)


# ----------------------------------------------------
# [디스코드 이벤트 및 명령어]
# ----------------------------------------------------
@bot.event
async def on_ready():
    print(f"🤖 봇이 성공적으로 로그인했습니다: {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"✨ {len(synced)}개의 슬래시 명령어가 동기화되었습니다.")
    except Exception as e:
        print(f"❌ 명령어 동기화 중 에러 발생: {e}")

@bot.tree.command(name="ping", description="봇이 살아있는지 확인합니다.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! I'm here.")

@bot.tree.command(name="join", description="명령어를 사용한 유저의 음성 채널에 접속합니다.")
async def join(interaction: discord.Interaction):
    if interaction.user.voice and interaction.user.voice.channel:
        voice_channel = interaction.user.voice.channel
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(voice_channel)
        else:
            await voice_channel.connect()
        await interaction.response.send_message(f"🎤 {voice_channel.name} 음성 채널에 성공적으로 접속했습니다!")
    else:
        await interaction.response.send_message("⚠️ 이 명령어를 사용하려면 먼저 음성 채널에 들어가 있으셔야 해요!", ephemeral=True)

@bot.tree.command(name="상점", description="오늘 내 발로란트 상점을 확인합니다.")
async def shop_command(interaction: discord.Interaction):
    # 비밀번호 입력을 위해 모달 팝업 창 띄우기
    await interaction.response.send_modal(ValorantAuthModal())

# 봇 실행
keep_alive()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ 에러: DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.")

import os
import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask
from threading import Thread
import requests

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
# [라이엇 인증 및 상점 로직 Class]
# ----------------------------------------------------
class ValorantAPI:
    def __init__(self):
        self.session = requests.Session()
        self.headers = {'User-Agent': 'RiotClient/43.0.1.4195386.4190634 rso-auth (Windows;10;10.0.19041.1.256.64bit)'}

    def login(self, username, password, mfa_code=None):
        auth_body = {
            "client_id": "play-valorant-web-prod",
            "nonce": "1",
            "redirect_uri": "https://playvalorant.com/opt_in",
            "response_type": "token id_token",
        }
        self.session.post('https://auth.riotgames.com/api/v1/authorization', json=auth_body, headers=self.headers)
        
        login_body = {
            "type": "auth",
            "username": username,
            "password": password,
            "remember": "true"
        }
        
        res = self.session.put('https://auth.riotgames.com/api/v1/authorization', json=login_body, headers=self.headers).json()
        print(f"[DEBUG] 라이엇 로그인 응답: {res}") # Render 대시보드 로그 확인용

        # 1. 이미 2FA 코드가 함께 전달되었을 때의 처리
        if mfa_code:
            mfa_body = {
                "type": "multifactor",
                "code": mfa_code,
                "rememberDevice": True
            }
            res = self.session.put('https://auth.riotgames.com/api/v1/authorization', json=mfa_body, headers=self.headers).json()
            print(f"[DEBUG] 2FA 제출 응답: {res}")

        # 2. 2단계 인증(2FA) 감지 로직 강화
        is_mfa = False
        if res.get('type') == 'multifactor':
            is_mfa = True
        elif res.get('error') == 'multifactor_attempt':
            is_mfa = True
        elif 'response' in res and 'parameters' in res['response']:
            uri_str = str(res['response']['parameters'])
            if 'multifactor' in uri_str or 'sub-multifactor' in uri_str:
                is_mfa = True

        if is_mfa:
            return "2FA_REQUIRED", None

        # 3. 일반 로그인 실패 처리
        if 'error' in res or 'response' not in res or 'parameters' not in res.get('response', {}):
            return "ERROR", "❌ 로그인 실패: 아이디/비밀번호가 틀렸거나 2FA 인증에 실패했습니다."

        try:
            uri = res['response']['parameters']['uri']
            access_token = uri.split('access_token=')[1].split('&')[0]

            ent_res = self.session.post(
                'https://entitlements.auth.riotgames.com/api/token/v1', 
                headers={'Authorization': f'Bearer {access_token}'}
            ).json()
            entitlements_token = ent_res['entitlements_token']

            user_res = self.session.post(
                'https://auth.riotgames.com/userinfo', 
                headers={'Authorization': f'Bearer {access_token}'}
            ).json()
            puuid = user_res['sub']

            # 상점 정보 요청
            shop_headers = {
                'Authorization': f'Bearer {access_token}',
                'X-Riot-Entitlements-JWT': entitlements_token,
            }
            shop_url = f'https://pd.kr.a.pvp.net/store/v2/storefront/{puuid}'
            shop_data = self.session.get(shop_url, headers=shop_headers).json()

            single_item_offers = shop_data['SkinsPanelLayout']['SingleItemOffers']
            
            skins_info = []
            for skin_id in single_item_offers:
                skin_res = requests.get(f'https://valorant-api.com/v1/weapons/skinlevels/{skin_id}?language=ko-KR').json()
                if skin_res['status'] == 200:
                    skins_info.append({
                        'name': skin_res['data']['displayName'],
                        'icon': skin_res['data']['displayIcon']
                    })

            return "SUCCESS", skins_info
        except Exception as e:
            return "ERROR", f"❌ 상점 정보 처리 중 오류가 발생했습니다: {e}"

# ----------------------------------------------------
# [2단계 인증(2FA) 입력 모달]
# ----------------------------------------------------
class TwoFactorModal(discord.ui.Modal, title="2단계 인증(2FA) 코드 입력"):
    code = discord.ui.TextInput(
        label="이메일로 전송된 6자리 인증 코드",
        placeholder="123456",
        required=True,
        max_length=6
    )

    def __init__(self, username, password):
        super().__init__()
        self.username = username
        self.password = password

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        api = ValorantAPI()
        status, result = api.login(self.username, self.password, mfa_code=self.code.value)
        
        if status == "ERROR":
            await interaction.followup.send(content=result, ephemeral=True)
            return

        embed = discord.Embed(
            title="🛒 오늘의 발로란트 일일 상점", 
            color=discord.Color.red(),
            description="본인에게만 보이는 상점 정보입니다."
        )
        for idx, skin in enumerate(result, 1):
            embed.add_field(name=f"{idx}. {skin['name']}", value="\u200b", inline=False)
        if result and result[-1]['icon']:
            embed.set_thumbnail(url=result[-1]['icon'])

        await interaction.followup.send(embed=embed, ephemeral=True)

# ----------------------------------------------------
# [기본 로그인 모달]
# ----------------------------------------------------
class ValorantAuthModal(discord.ui.Modal, title="발로란트 로그인"):
    username = discord.ui.TextInput(label="라이엇 계정 ID", placeholder="아이디를 입력하세요", required=True)
    password = discord.ui.TextInput(label="비밀번호", placeholder="비밀번호를 입력하세요", style=discord.TextStyle.short, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        api = ValorantAPI()
        status, result = api.login(self.username.value, self.password.value)

        # 2단계 인증 요구 감지 시 2차 입력 모달 팝업 표시
        if status == "2FA_REQUIRED":
            await interaction.response.send_modal(
                TwoFactorModal(username=self.username.value, password=self.password.value)
            )
            return

        await interaction.response.defer(ephemeral=True)

        if status == "ERROR":
            await interaction.followup.send(content=result, ephemeral=True)
            return

        embed = discord.Embed(
            title="🛒 오늘의 발로란트 일일 상점", 
            color=discord.Color.red(),
            description="본인에게만 보이는 상점 정보입니다."
        )
        for idx, skin in enumerate(result, 1):
            embed.add_field(name=f"{idx}. {skin['name']}", value="\u200b", inline=False)
        if result and result[-1]['icon']:
            embed.set_thumbnail(url=result[-1]['icon'])

        await interaction.followup.send(embed=embed, ephemeral=True)

# ----------------------------------------------------
# [디스코드 이벤트 및 명령어]
# ----------------------------------------------------
@bot.event
async def on_ready():
    print(f"🤖 봇 로그인 성공: {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"✨ {len(synced)}개 명령어 동기화 완료")
    except Exception as e:
        print(f"❌ 동기화 에러: {e}")

@bot.tree.command(name="ping", description="봇이 살아있는지 확인합니다.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! I'm here.")

@bot.tree.command(name="join", description="음성 채널에 접속합니다.")
async def join(interaction: discord.Interaction):
    if interaction.user.voice and interaction.user.voice.channel:
        voice_channel = interaction.user.voice.channel
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(voice_channel)
        else:
            await voice_channel.connect()
        await interaction.response.send_message(f"🎤 {voice_channel.name} 접속 완료!")
    else:
        await interaction.response.send_message("⚠️ 음성 채널에 먼저 입장해 주세요.", ephemeral=True)

@bot.tree.command(name="상점", description="오늘 내 발로란트 상점을 확인합니다.")
async def shop_command(interaction: discord.Interaction):
    await interaction.response.send_modal(ValorantAuthModal())

keep_alive()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.")

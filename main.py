import os
import discord
from discord.ext import commands
from flask import Flask
from threading import Thread

# 봇 설정
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 가짜 웹 서버 설정 (Render가 깨어있게 만들기 위함)
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run)
    t.start()

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

# 봇 실행 전 가짜 웹 서버 실행
keep_alive()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ 에러: DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.")

import os
import discord
from discord.ext import commands

# 봇의 명령어 접두사(Prefix) 설정 및 인텐트 설정
# 슬래시(/) 명령어를 사용하더라도 기본 인텐트는 필요합니다.
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"🤖 봇이 성공적으로 로그인했습니다: {bot.user.name}")
    try:
        # 슬래시 명령어를 디스코드 서버에 동기화합니다.
        synced = await bot.tree.sync()
        print(f"✨ {len(synced)}개의 슬래시 명령어가 동기화되었습니다.")
    except Exception as e:
        print(f"❌ 명령어 동기화 중 에러 발생: {e}")

# 1. 기존에 잘 작동하던 /ping 명령어
@bot.tree.command(name="ping", description="봇이 살아있는지 확인합니다.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! I'm here.")

# 2. 에러를 해결한 /join 명령어
@bot.tree.command(name="join", description="명령어를 사용한 유저의 음성 채널에 접속합니다.")
async def join(interaction: discord.Interaction):
    # 명령어를 입력한 유저의 음성 채널 상태 확인
    if interaction.user.voice and interaction.user.voice.channel:
        voice_channel = interaction.user.voice.channel
        
        # 봇이 이미 다른 음성 채널에 들어가 있는지 확인
        if interaction.guild.voice_client:
            # 이미 들어가 있다면 해당 채널로 이동
            await interaction.guild.voice_client.move_to(voice_channel)
        else:
            # 들어가 있지 않다면 새로 접속
            await voice_channel.connect()
            
        await interaction.response.send_message(f"🎤 {voice_channel.name} 음성 채널에 성공적으로 접속했습니다!")
    else:
        # 유저가 음성 채널에 들어가 있지 않은 경우 경고 메시지 반환
        await interaction.response.send_message("⚠️ 이 명령어를 사용하려면 먼저 음성 채널에 들어가 있으셔야 해요!", ephemeral=True)

# Render 환경변수에 등록할 토큰을 가져와 봇을 실행합니다.
# 토큰 값은 코드에 직접 적지 않고 Render에서 따로 등록할 예정입니다.
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ 에러: DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.")
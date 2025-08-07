import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import re
import json

# 配置设置
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}
YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'simulate': True,
    'preferredquality': '192',
    'preferredcodec': 'mp3',
    'key': 'FFmpegExtractAudio',
    'quiet': True,
    'no_warnings': True,
    'cookiefile': 'cookies.txt'  # 可选，用于解决网易云音乐地区限制
}
NETEASE_PLAYLIST_OPTIONS = {
    'extract_flat': True,
    'quiet': True,
    'force_generic_extractor': True,
}

class MusicQueue:
    """管理音乐队列的类"""
    def __init__(self):
        self.queue = []
        self.current_song = None
        self.loop = asyncio.get_event_loop()
        self.volume = 0.5

    def add_song(self, song):
        """添加歌曲到队列"""
        self.queue.append(song)
        return f"✅ 已添加 **{song['title']}** 到队列 (#{len(self.queue)})"

    def add_playlist(self, playlist):
        """添加整个歌单到队列"""
        count = len(playlist)
        self.queue.extend(playlist)
        return f"🎵 已添加歌单 ({count}首歌曲) 到队列"

    def next_song(self):
        """获取下一首歌曲"""
        if self.queue:
            self.current_song = self.queue.pop(0)
            return self.current_song
        return None

    def clear_queue(self):
        """清空队列"""
        self.queue = []
        self.current_song = None

    def get_queue_list(self):
        """获取队列列表"""
        if not self.queue and not self.current_song:
            return "队列为空"
        
        info = []
        if self.current_song:
            info.append(f"**正在播放:** {self.current_song['title']}")
        
        for i, song in enumerate(self.queue, 1):
            info.append(f"{i}. {song['title']}")
        
        return "\n".join(info)

class MusicBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}  # 按服务器ID存储队列
        self.voice_clients = {}
        
    def get_queue(self, guild_id):
        """获取或创建指定服务器的队列"""
        if guild_id not in self.queues:
            self.queues[guild_id] = MusicQueue()
        return self.queues[guild_id]
    
    async def play_next(self, guild_id, error=None):
        """播放下一首歌曲的回调函数"""
        if error:
            print(f"播放错误: {error}")
        
        queue = self.get_queue(guild_id)
        next_song = queue.next_song()
        
        if next_song:
            # 更新机器人状态
            await self.bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.listening,
                    name=next_song['title'][:128]
                )
            )
            
            # 播放音频
            source = discord.FFmpegOpusAudio(next_song['url'], **FFMPEG_OPTIONS)
            self.voice_clients[guild_id].play(
                source, 
                after=lambda e: asyncio.run_coroutine_threadsafe(self.play_next(guild_id, e), queue.loop)
            )
        else:
            # 队列为空时重置状态
            await self.bot.change_presence(activity=discord.Game(name="/help"))
    
    async def search_youtube(self, query):
        """搜索YouTube并返回最佳结果"""
        with youtube_dl.YoutubeDL(YDL_OPTIONS) as ydl:
            try:
                info = ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]
                return {
                    'url': info['url'],
                    'title': info['title'],
                    'duration': info.get('duration', 0)
                }
            except Exception as e:
                print(f"搜索错误: {e}")
                return None

    async def extract_netease_playlist(self, playlist_url):
        """提取网易云音乐歌单内容"""
        try:
            with youtube_dl.YoutubeDL(NETEASE_PLAYLIST_OPTIONS) as ydl:
                # 获取歌单信息
                info = ydl.extract_info(playlist_url, download=False)
                
                if not info or 'entries' not in info:
                    return None, "无法解析歌单"
                
                # 提取歌单标题和歌曲列表
                playlist_title = info.get('title', '未知歌单')
                songs = []
                
                # 处理歌单中的每首歌曲
                for entry in info['entries']:
                    if not entry:
                        continue
                    
                    # 构建搜索查询
                    title = entry.get('title', '未知歌曲')
                    artist = entry.get('uploader', '未知艺术家')
                    search_query = f"{title} {artist}"
                    
                    # 搜索YouTube对应歌曲
                    song = await self.search_youtube(search_query)
                    if song:
                        songs.append(song)
                
                return songs, playlist_title
        except Exception as e:
            print(f"歌单解析错误: {e}")
            return None, f"解析失败: {str(e)}"

    @app_commands.command(name="play", description="播放音乐、歌单或添加到队列")
    async def play(self, interaction: discord.Interaction, query: str):
        """播放命令"""
        await interaction.response.defer()
        
        # 检查用户是否在语音频道
        if not interaction.user.voice:
            return await interaction.followup.send("❌ 请先加入语音频道！")
        
        # 获取或创建队列
        queue = self.get_queue(interaction.guild_id)
        
        # 检查是否为网易云歌单链接
        netease_pattern = r"https?://music\.163\.com/.*#/playlist\?id=\d+"
        if re.match(netease_pattern, query):
            # 处理网易云歌单
            message = await interaction.followup.send("⏳ 正在解析网易云歌单，请稍候...")
            
            songs, playlist_title = await self.extract_netease_playlist(query)
            if not songs:
                return await message.edit(content=f"❌ 无法添加歌单: {playlist_title}")
            
            # 添加歌单中的所有歌曲
            queue.add_playlist(songs)
            
            # 连接语音频道（如果尚未连接）
            try:
                if interaction.guild_id not in self.voice_clients:
                    self.voice_clients[interaction.guild_id] = await interaction.user.voice.channel.connect()
            except discord.Forbidden:
                return await message.edit(content="❌ 我没有权限加入语音频道！请检查我的权限设置。")
            except discord.ClientException as e:
                return await message.edit(content=f"❌ 连接错误: {str(e)}")
            
            # 如果没有正在播放，立即开始播放
            if not self.voice_clients[interaction.guild_id].is_playing():
                await self.play_next(interaction.guild_id)
            
            return await message.edit(content=f"🎵 已添加歌单 **{playlist_title}** ({len(songs)}首歌曲) 到队列")
        
        # 处理普通歌曲查询
        song = await self.search_youtube(query)
        if not song:
            return await interaction.followup.send("🔍 找不到歌曲")
        
        try:
            # 连接语音频道
            if interaction.guild_id not in self.voice_clients:
                self.voice_clients[interaction.guild_id] = await interaction.user.voice.channel.connect()
            
            # 添加歌曲到队列
            response = queue.add_song(song)
            await interaction.followup.send(response)
            
            # 如果没有正在播放，立即开始播放
            if not self.voice_clients[interaction.guild_id].is_playing():
                await self.play_next(interaction.guild_id)
                
        except discord.Forbidden:
            await interaction.followup.send("❌ 我没有权限加入语音频道！请检查我的权限设置。")
        except discord.ClientException as e:
            await interaction.followup.send(f"❌ 连接错误: {str(e)}")

    @app_commands.command(name="skip", description="跳过当前歌曲")
    async def skip(self, interaction: discord.Interaction):
        """跳过命令"""
        if interaction.guild_id in self.voice_clients and self.voice_clients[interaction.guild_id].is_playing():
            self.voice_clients[interaction.guild_id].stop()
            await interaction.response.send_message("⏭️ 已跳过当前歌曲")
        else:
            await interaction.response.send_message("❌ 没有正在播放的音乐")

    @app_commands.command(name="stop", description="停止播放并清空队列")
    async def stop(self, interaction: discord.Interaction):
        """停止命令"""
        if interaction.guild_id in self.voice_clients:
            self.get_queue(interaction.guild_id).clear_queue()
            self.voice_clients[interaction.guild_id].stop()
            await self.voice_clients[interaction.guild_id].disconnect()
            del self.voice_clients[interaction.guild_id]
            await self.bot.change_presence(activity=discord.Game(name="/help"))
            await interaction.response.send_message("⏹️ 已停止播放")
        else:
            await interaction.response.send_message("❌ 机器人未在语音频道中")

    @app_commands.command(name="queue", description="显示当前队列")
    async def show_queue(self, interaction: discord.Interaction):
        """显示队列命令"""
        queue = self.get_queue(interaction.guild_id)
        queue_list = queue.get_queue_list()
        await interaction.response.send_message(f"🎶 当前队列:\n{queue_list}")

    @app_commands.command(name="pause", description="暂停播放")
    async def pause(self, interaction: discord.Interaction):
        """暂停命令"""
        if interaction.guild_id in self.voice_clients and self.voice_clients[interaction.guild_id].is_playing():
            self.voice_clients[interaction.guild_id].pause()
            await interaction.response.send_message("⏸️ 已暂停")
        else:
            await interaction.response.send_message("❌ 没有正在播放的音乐")

    @app_commands.command(name="resume", description="继续播放")
    async def resume(self, interaction: discord.Interaction):
        """继续命令"""
        if interaction.guild_id in self.voice_clients and self.voice_clients[interaction.guild_id].is_paused():
            self.voice_clients[interaction.guild_id].resume()
            await interaction.response.send_message("▶️ 继续播放")
        else:
            await interaction.response.send_message("❌ 音乐未暂停")

    @app_commands.command(name="help", description="显示所有可用命令和所需权限")
    async def help(self, interaction: discord.Interaction):
        """帮助命令"""
        # 创建嵌入消息
        embed = discord.Embed(
            title="🎵 音乐机器人帮助",
            description="支持播放YouTube音乐和网易云音乐歌单\n\n**所有可用命令和所需权限:**",
            color=discord.Color.blue()
        )
        
        # 添加命令信息
        commands_list = [
            ("`/play [歌曲名/URL]`", "播放音乐或添加到队列\n支持网易云歌单链接"),
            ("`/skip`", "跳过当前播放的歌曲"),
            ("`/stop`", "停止播放并清空队列"),
            ("`/queue`", "显示当前播放队列"),
            ("`/pause`", "暂停当前播放"),
            ("`/resume`", "继续播放暂停的音乐"),
            ("`/help`", "显示此帮助信息")
        ]
        
        for name, value in commands_list:
            embed.add_field(name=name, value=value, inline=False)
        
        # 添加权限信息
        embed.add_field(
            name="所需权限",
            value="• 发送消息\n• 嵌入链接\n• 查看频道\n• 连接语音频道\n• 在语音频道说话\n• 使用应用命令",
            inline=False
        )
        
        # 添加使用提示
        embed.set_footer(text="网易云音乐歌单可能需要代理才能正常访问")
        
        await interaction.response.send_message(embed=embed)

# 设置机器人
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"登录为 {bot.user}")
    print(f"机器人ID: {bot.user.id}")
    print("所需权限值: 3148864")
    print(f"邀请链接: https://discord.com/api/oauth2/authorize?client_id={bot.user.id}&permissions=3148864&scope=bot%20applications.commands")
    
    await bot.change_presence(activity=discord.Game(name="/help"))
    try:
        synced = await bot.tree.sync()
        print(f"已同步 {len(synced)} 个命令")
    except Exception as e:
        print(f"命令同步错误: {e}")

@bot.event
async def on_guild_join(guild):
    """当机器人加入新服务器时发送欢迎消息"""
    # 查找第一个文本频道
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            try:
                embed = discord.Embed(
                    title="🎵 感谢添加音乐机器人!",
                    description="支持播放YouTube音乐和网易云音乐歌单\n\n使用 `/help` 查看所有可用命令\n\n"
                                "请确保我拥有以下权限:\n"
                                "- 发送消息\n- 嵌入链接\n- 查看频道\n- 连接语音频道\n- 在语音频道说话\n- 使用应用命令",
                    color=discord.Color.green()
                )
                await channel.send(embed=embed)
            except:
                continue
            break

async def main():
    async with bot:
        await bot.add_cog(MusicBot(bot))
        await bot.start("YOUR_BOT_TOKEN_HERE")  # 替换为你的机器人Token

if __name__ == "__main__":
    asyncio.run(main())
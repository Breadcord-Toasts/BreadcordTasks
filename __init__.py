import random
import sqlite3
import time
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import tasks

import breadcord


class RemindModal(discord.ui.Modal, title="Register reminder"):
    time = discord.ui.TextInput(label="In how long you should be reminded", placeholder="1d 12h 30m")
    content = discord.ui.TextInput(
        label="Content",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=4000,
        placeholder=random.choice(
            [
                "Remember to feed the ducks",
                "Remember to take the cat for a walk",
                "Remember to take the bread out of the oven",
            ]
        ),
    )

    def __init__(self) -> None:
        super().__init__()
        self.interaction = None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class BreadcordTasks(breadcord.module.ModuleCog):
    def __init__(self, module_id: str):
        super().__init__(module_id)
        self.connection = sqlite3.connect(self.module.storage_path / "tasks.db")
        self.cursor = self.connection.cursor()
        self.cursor.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            "   task_due_time INTEGER NOT NULL,"
            "   author_id INTEGER NOT NULL,"
            "   channel_id INTEGER NOT NULL,"
            "   task_content"
            ")"
        )
        self.cursor.execute(
            "CREATE TABLE IF NOT EXISTS bookmarks ("
            "   bookmarked_message_id INTEGER NOT NULL,"
            "   bookmarked_message_channel_id INTEGER NOT NULL,"
            "   bookmarked_message_guild_id NOT NULL,"
            "   bookmarker INTEGER NOT NULL,"
            "   added_at INTEGER NOT NULL"
            ")"
        )

        self.check_reminds.start()

    def cog_unload(self):
        self.check_reminds.cancel()

    @app_commands.command(description="Reminds you about something in some amount of time")
    async def remind(self, interaction: discord.Interaction) -> None:
        modal = RemindModal()
        await interaction.response.send_modal(modal)
        await modal.wait()

        allowed_letters = ["d", "h", "m", "s"]
        time_dict = dict.fromkeys(allowed_letters, 0)
        for time_segment in str(modal.time).strip().split():
            try:
                time_dict[time_segment[-1]] += int(time_segment[:-1])
            except (KeyError, ValueError):
                return await modal.interaction.response.send_message("Invalid time passed.", ephemeral=True)

        now = datetime.now()
        then = now + timedelta(
            days=time_dict["d"],
            hours=time_dict["h"],
            minutes=time_dict["m"],
            seconds=time_dict["s"],
        )
        then_timestamp = int(time.mktime(then.timetuple()))

        self.cursor.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?)",
            (then_timestamp, interaction.user.id, interaction.channel.id, str(modal.content)),
        )
        self.connection.commit()
        await modal.interaction.response.send_message(
            f"Reminder set for <t:{then_timestamp}> (<t:{then_timestamp}:R>)", ephemeral=True
        )

    @tasks.loop(seconds=30.0)
    async def check_reminds(self) -> None:
        now = int(time.mktime(datetime.now().timetuple()))
        response = self.cursor.execute(
            "SELECT task_due_time, author_id, channel_id, task_content FROM tasks WHERE task_due_time < ?", (now,)
        ).fetchall()

        for task_due_time, author_id, channel_id, task_content in response:
            author = await self.bot.fetch_user(author_id)

            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.Forbidden:
                channel = author.dm_channel

            embed = discord.Embed(title=f"⏰ You set a reminder for <t:{task_due_time}>", description=task_content)
            await channel.send(author.mention, embed=embed)
            self.cursor.execute(
                "DELETE FROM tasks WHERE task_due_time = ? AND author_id = ? AND channel_id = ? AND task_content = ?",
                (task_due_time, author_id, channel_id, task_content),
            )
            self.connection.commit()

    @breadcord.module.ModuleCog.listener()
    async def on_raw_reaction_add(self, reaction: discord.RawReactionActionEvent) -> None:
        if str(reaction.emoji) not in self.settings.bookmark_emojis.value:
            return
        self.cursor.execute(
            "INSERT INTO bookmarks VALUES (?, ?, ?, ?, ?)",
            (
                reaction.message_id,
                reaction.channel_id,
                "@me" if reaction.guild_id is None else reaction.guild_id,
                reaction.user_id,
                int(time.mktime(datetime.now().timetuple())),
            ),
        )
        self.connection.commit()

    @breadcord.module.ModuleCog.listener()
    async def on_raw_reaction_remove(self, reaction: discord.RawReactionActionEvent) -> None:
        if str(reaction.emoji) not in self.settings.bookmark_emojis.value:
            return
        self.cursor.execute(
            "DELETE FROM bookmarks WHERE bookmarked_message_id = ? AND bookmarker = ?",
            (reaction.message_id, reaction.user_id),
        )
        self.connection.commit()

    @app_commands.command(description="Sends a list of your bookmarked messages.")
    async def bookmarks(self, interaction: discord.Interaction) -> None:
        bookmarks = self.cursor.execute(
            "SELECT bookmarked_message_id, bookmarked_message_channel_id, bookmarked_message_guild_id, added_at "
            "FROM bookmarks "
            "WHERE bookmarker = ? "
            "ORDER BY added_at ASC ",
            (interaction.user.id,),
        ).fetchall()
        if not bookmarks:
            return await interaction.response.send_message("You don't currently have any bookmarks.", ephemeral=True)

        embed = discord.Embed(
            title="Your bookmarks",
            description="\n".join(
                f"[🔖 {bookmark[0]}](<https://discord.com/channels/{bookmark[2]}/{bookmark[1]}/{bookmark[0]}>) "
                f"added <t:{bookmark[3]}:R>"
                for bookmark in bookmarks
            )[:4000] # Temporary until pagination is added
        )
        embed.set_footer(text="Sorted oldest first, newest last")
        # TODO: Pagintion
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: breadcord.Bot):
    await bot.add_cog(BreadcordTasks("breadcord_tasks"))

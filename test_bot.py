from datetime import date, timedelta
import logging
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telegram import MessageEntity
from telegram.constants import ChatType
from telegram.error import TelegramError

from telegram_spam_bot.bot import (
    BotConfig,
    SecretRedactionFilter,
    UsageStore,
    custom_limit_message,
    help_command,
    limit_callback,
    limit_command,
    message_is_spam,
    moderate_group_message,
    start_command,
)


class SpamDetectionTests(unittest.TestCase):
    def test_detects_http_urls_and_bare_domains(self) -> None:
        self.assertTrue(message_is_spam(SimpleNamespace(
            photo=None,
            text="Visit https://example.com/page",
            caption=None,
            entities=None,
            caption_entities=None,
        )))
        self.assertTrue(message_is_spam(SimpleNamespace(
            photo=None,
            text="More info at example.org",
            caption=None,
            entities=None,
            caption_entities=None,
        )))

    def test_detects_links_hidden_in_telegram_entities(self) -> None:
        hidden_link = MessageEntity(
            type=MessageEntity.TEXT_LINK,
            offset=0,
            length=4,
            url="https://example.com",
        )
        self.assertTrue(message_is_spam(SimpleNamespace(
            photo=None,
            text="Read this",
            caption=None,
            entities=[hidden_link],
            caption_entities=None,
        )))

    def test_detects_photo_and_photo_caption_links(self) -> None:
        self.assertTrue(message_is_spam(SimpleNamespace(
            photo=[object()],
            text=None,
            caption=None,
            entities=None,
            caption_entities=None,
        )))
        self.assertTrue(message_is_spam(SimpleNamespace(
            photo=None,
            text=None,
            caption="See example.net",
            entities=None,
            caption_entities=None,
        )))

    def test_ignores_regular_text(self) -> None:
        self.assertFalse(message_is_spam(SimpleNamespace(
            photo=None,
            text="Hello, how are you?",
            caption=None,
            entities=None,
            caption_entities=None,
        )))

    def test_redacts_bot_token_from_request_logs(self) -> None:
        fake_token = "123456:fake-token-for-test-only"
        record = logging.LogRecord(
            "httpx",
            logging.INFO,
            "",
            0,
            "Request URL includes bot%s/getMe",
            (fake_token,),
            None,
        )

        SecretRedactionFilter(fake_token).filter(record)

        self.assertNotIn(fake_token, record.getMessage())
        self.assertIn("[REDACTED]", record.getMessage())


class DailyUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = UsageStore(":memory:")
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()

    def test_allows_limit_then_rejects_later_messages(self) -> None:
        day = date(2026, 10, 3)
        first = self.store.record_spam(-100, 42, day, 2)
        second = self.store.record_spam(-100, 42, day, 2)
        third = self.store.record_spam(-100, 42, day, 2)
        fourth = self.store.record_spam(-100, 42, day, 2)

        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertFalse(third.allowed)
        self.assertFalse(fourth.allowed)
        self.assertEqual(self.store.get_count(-100, 42, day), 4)

    def test_counts_are_separate_by_chat_member_and_day(self) -> None:
        day = date(2026, 10, 3)
        self.store.record_spam(-100, 42, day, 3)

        self.assertEqual(self.store.get_count(-100, 43, day), 0)
        self.assertEqual(self.store.get_count(-101, 42, day), 0)
        self.assertEqual(self.store.get_count(-100, 42, day + timedelta(days=1)), 0)

    def test_zero_limit_rejects_first_spam_message(self) -> None:
        decision = self.store.record_spam(-100, 42, date(2026, 10, 3), 0)
        self.assertFalse(decision.allowed)

    def test_old_usage_is_cleaned_up_when_new_messages_are_recorded(self) -> None:
        today = date(2026, 10, 3)
        old_day = today - timedelta(days=46)
        self.store.record_spam(-100, 42, old_day, 1)

        self.store.record_spam(-100, 43, today, 1)

        self.assertEqual(self.store.get_count(-100, 42, old_day), 0)

    def test_group_limits_override_default_separately(self) -> None:
        self.store.register_group(-100, "First group")
        self.store.register_group(-101, "Second group")
        self.store.set_daily_limit(-100, 3)

        self.assertEqual(self.store.get_daily_limit(-100, 1), 3)
        self.assertEqual(self.store.get_daily_limit(-101, 1), 1)


class SilentModerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.store = UsageStore(":memory:")
        self.store.initialize()
        self.config = BotConfig(
            token="test-token",
            daily_limit=1,
            timezone_name="Europe/Rome",
            database_path=":memory:",
        )

    def tearDown(self) -> None:
        self.store.close()

    async def test_over_limit_message_is_deleted_without_group_reply(self) -> None:
        application = SimpleNamespace(
            bot_data={"usage_store": self.store, "config": self.config}
        )
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(
                return_value=SimpleNamespace(status="member")
            ),
            send_message=AsyncMock(),
        )
        context = SimpleNamespace(bot=bot, application=application)

        def make_update() -> SimpleNamespace:
            message = SimpleNamespace(
                photo=None,
                text="https://example.org",
                caption=None,
                entities=None,
                caption_entities=None,
                message_thread_id=None,
                delete=AsyncMock(),
            )
            return SimpleNamespace(
                effective_message=message,
                effective_chat=SimpleNamespace(
                    id=-100,
                    type=ChatType.SUPERGROUP,
                ),
                effective_user=SimpleNamespace(id=42),
            )

        first_update = make_update()
        second_update = make_update()
        await moderate_group_message(first_update, context)
        await moderate_group_message(second_update, context)

        first_update.effective_message.delete.assert_not_awaited()
        second_update.effective_message.delete.assert_awaited_once()
        bot.send_message.assert_not_awaited()

    async def test_start_command_does_not_reply_in_a_group(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type=ChatType.SUPERGROUP),
        )

        await start_command(update, None)

        message.reply_text.assert_not_awaited()

    async def test_start_and_help_reply_in_private_chats(self) -> None:
        for command in (start_command, help_command):
            with self.subTest(command=command.__name__):
                message = SimpleNamespace(reply_text=AsyncMock())
                update = SimpleNamespace(
                    effective_message=message,
                    effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
                )

                await command(update, None)

                message.reply_text.assert_awaited_once()

    async def test_limit_command_registers_group_without_posting(self) -> None:
        application = SimpleNamespace(bot_data={"usage_store": self.store})
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(
                id=-100,
                title="Test group",
                type=ChatType.SUPERGROUP,
            ),
            effective_user=SimpleNamespace(id=42),
        )

        await limit_command(update, SimpleNamespace(application=application))

        message.reply_text.assert_not_awaited()
        self.assertEqual(self.store.list_groups(), [(-100, "Test group")])

    async def test_private_limit_menu_lists_only_groups_user_administers(self) -> None:
        self.store.register_group(-100, "Admin group")
        self.store.register_group(-101, "Member group")
        application = SimpleNamespace(
            bot_data={"usage_store": self.store, "config": self.config}
        )
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(
                side_effect=[
                    SimpleNamespace(status="administrator"),
                    SimpleNamespace(status="member"),
                ]
            )
        )
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            effective_user=SimpleNamespace(id=42),
        )

        await limit_command(
            update,
            SimpleNamespace(application=application, bot=bot),
        )

        message.reply_text.assert_awaited_once()
        markup = message.reply_text.await_args.kwargs["reply_markup"]
        callback_data = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertEqual(callback_data, ["limit:group:-100"])

    async def test_limit_callback_saves_selected_group_limit(self) -> None:
        self.store.register_group(-100, "Test group")
        application = SimpleNamespace(
            bot_data={"usage_store": self.store, "config": self.config}
        )
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(
                return_value=SimpleNamespace(status="administrator")
            )
        )
        query = SimpleNamespace(
            data="limit:set:-100:3",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(
                chat=SimpleNamespace(type=ChatType.PRIVATE)
            ),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )

        await limit_callback(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(application=application, bot=bot, user_data={}),
        )

        self.assertEqual(self.store.get_daily_limit(-100, 1), 3)
        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()

    async def test_custom_limit_can_be_entered_in_private_chat(self) -> None:
        self.store.register_group(-100, "Test group")
        application = SimpleNamespace(
            bot_data={"usage_store": self.store, "config": self.config}
        )
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(
                return_value=SimpleNamespace(status="administrator")
            )
        )
        user_data = {}
        query = SimpleNamespace(
            data="limit:custom:-100",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(
                chat=SimpleNamespace(type=ChatType.PRIVATE)
            ),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            application=application,
            bot=bot,
            user_data=user_data,
        )

        await limit_callback(SimpleNamespace(callback_query=query), context)
        message = SimpleNamespace(
            text="7",
            reply_text=AsyncMock(),
        )
        await custom_limit_message(
            SimpleNamespace(
                effective_message=message,
                effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
                effective_user=SimpleNamespace(id=42),
            ),
            context,
        )

        self.assertEqual(self.store.get_daily_limit(-100, 1), 7)
        self.assertNotIn("pending_limit_chat_id", user_data)
        message.reply_text.assert_awaited_once()

    async def test_non_admin_cannot_change_group_limit(self) -> None:
        self.store.register_group(-100, "Test group")
        application = SimpleNamespace(
            bot_data={"usage_store": self.store, "config": self.config}
        )
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(
                return_value=SimpleNamespace(status="member")
            )
        )
        query = SimpleNamespace(
            data="limit:set:-100:5",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(
                chat=SimpleNamespace(type=ChatType.PRIVATE)
            ),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )

        await limit_callback(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(application=application, bot=bot, user_data={}),
        )

        self.assertEqual(self.store.get_daily_limit(-100, 1), 1)
        query.edit_message_text.assert_awaited_once()

    async def test_group_override_controls_moderation_independently(self) -> None:
        self.store.set_daily_limit(-100, 0)
        application = SimpleNamespace(
            bot_data={"usage_store": self.store, "config": self.config}
        )
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(
                return_value=SimpleNamespace(status="member")
            ),
            send_message=AsyncMock(),
        )

        def make_update(chat_id: int) -> SimpleNamespace:
            message = SimpleNamespace(
                photo=None,
                text="https://example.org",
                caption=None,
                entities=None,
                caption_entities=None,
                delete=AsyncMock(),
            )
            return SimpleNamespace(
                effective_message=message,
                effective_chat=SimpleNamespace(
                    id=chat_id,
                    type=ChatType.SUPERGROUP,
                ),
                effective_user=SimpleNamespace(id=42),
            )

        zero_limit_update = make_update(-100)
        default_limit_update = make_update(-101)
        context = SimpleNamespace(bot=bot, application=application)

        await moderate_group_message(zero_limit_update, context)
        await moderate_group_message(default_limit_update, context)

        zero_limit_update.effective_message.delete.assert_awaited_once()
        default_limit_update.effective_message.delete.assert_not_awaited()

    async def test_group_administrators_and_owners_are_exempt(self) -> None:
        for status in ("creator", "administrator"):
            with self.subTest(status=status):
                application = SimpleNamespace(
                    bot_data={"usage_store": self.store, "config": self.config}
                )
                bot = SimpleNamespace(
                    get_chat_member=AsyncMock(
                        return_value=SimpleNamespace(status=status)
                    ),
                    send_message=AsyncMock(),
                )
                message = SimpleNamespace(
                    photo=None,
                    text="https://example.org",
                    caption=None,
                    entities=None,
                    caption_entities=None,
                    delete=AsyncMock(),
                )
                update = SimpleNamespace(
                    effective_message=message,
                    effective_chat=SimpleNamespace(
                        id=-100,
                        type=ChatType.SUPERGROUP,
                    ),
                    effective_user=SimpleNamespace(id=42),
                )

                await moderate_group_message(
                    update,
                    SimpleNamespace(bot=bot, application=application),
                )

                message.delete.assert_not_awaited()
                self.assertEqual(
                    self.store.get_count(-100, 42, date.today()),
                    0,
                )
                bot.send_message.assert_not_awaited()

    async def test_role_lookup_error_skips_moderation(self) -> None:
        application = SimpleNamespace(
            bot_data={"usage_store": self.store, "config": self.config}
        )
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(side_effect=TelegramError("lookup failed")),
            send_message=AsyncMock(),
        )
        message = SimpleNamespace(
            photo=None,
            text="https://example.org",
            caption=None,
            entities=None,
            caption_entities=None,
            delete=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(id=-100, type=ChatType.SUPERGROUP),
            effective_user=SimpleNamespace(id=42),
        )

        await moderate_group_message(
            update,
            SimpleNamespace(bot=bot, application=application),
        )

        message.delete.assert_not_awaited()
        self.assertEqual(self.store.get_count(-100, 42, date.today()), 0)
        bot.send_message.assert_not_awaited()


class ConfigurationTests(unittest.TestCase):
    def test_default_daily_limit_is_one(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"}, clear=True):
            config = BotConfig.from_environment()

        self.assertEqual(config.daily_limit, 1)
        self.assertEqual(config.timezone_name, "Europe/Rome")

    def test_rejects_missing_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TELEGRAM_BOT_TOKEN is missing"):
                BotConfig.from_environment()

    def test_rejects_invalid_daily_limit(self) -> None:
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "test-token", "DAILY_SPAM_LIMIT": "many"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "must be a whole number"):
                BotConfig.from_environment()

    def test_rejects_negative_daily_limit(self) -> None:
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "test-token", "DAILY_SPAM_LIMIT": "-1"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "cannot be negative"):
                BotConfig.from_environment()

    def test_rejects_unknown_timezone(self) -> None:
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "test-token", "BOT_TIMEZONE": "Mars/Olympus"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "not a recognized timezone"):
                BotConfig.from_environment()


if __name__ == "__main__":
    unittest.main()
import unittest
from types import SimpleNamespace

from secretary.content import detect_kind, fingerprint, is_foreign, snapshot_from_message
from secretary.htmlfmt import entities_to_html
from secretary.i18n import locale
from secretary.notify import build_delete, build_edit, chat_link, event_header, labeled_header


def _user(uid=1, first="Михаил", username="Mifaaa93"):
    return SimpleNamespace(id=uid, is_bot=False, first_name=first, last_name=None, username=username)


def _chat(cid=10):
    return SimpleNamespace(id=cid, type="private", username="Mifaaa93", first_name="Михаил", last_name=None, title=None)


def _msg(**kwargs):
    base = dict(
        message_id=5,
        date=1_700_000_000,
        edit_date=None,
        chat=_chat(),
        from_user=_user(),
        text=None,
        caption=None,
        entities=None,
        caption_entities=None,
        photo=None,
        video=None,
        animation=None,
        video_note=None,
        voice=None,
        audio=None,
        document=None,
        sticker=None,
        live_photo=None,
        media_group_id=None,
        reply_to_message=None,
        has_protected_content=False,
        sender_business_bot=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class HeaderTests(unittest.TestCase):
    def test_username_is_only_a_link(self):
        user = {"id": 5, "first_name": "Михаил", "last_name": None, "username": "u", "is_bot": False}
        text = chat_link(user)
        self.assertIn('href="https://t.me/u"', text)
        self.assertIn(">Михаил</a>", text)
        self.assertNotIn(">https://t.me/u<", text)
        self.assertNotIn("user_id", text)
        self.assertNotIn("chat_id", text)
        self.assertNotIn("msg_id", text)

    def test_mention_covers_cyrillic_name(self):
        user = {"id": 5, "first_name": "Михаил", "last_name": None, "username": None, "is_bot": False}
        text = chat_link(user)
        self.assertIn('href="tg://user?id=5"', text)
        self.assertIn("Михаил", text)
        self.assertNotIn("user_id=", text)

    def test_unknown_user_has_no_mention(self):
        self.assertEqual(chat_link(None), "unknown")
        self.assertEqual(chat_link(None, lang="uk"), "невідомий")
        self.assertEqual(chat_link(None, lang="ru"), "неизвестный")

    def test_group_header_has_chat_and_sender(self):
        user = {"id": 5, "first_name": "Ann", "username": "ann", "is_bot": False}
        chat = {"id": -1001, "type": "supergroup", "title": "Work", "username": "workchat"}
        html = event_header(user, chat, "en")
        self.assertIn("Work", html)
        self.assertIn("https://t.me/workchat", html)
        self.assertIn("Ann", html)
        self.assertIn("From:", html)

    def test_event_label(self):
        user = {"id": 5, "first_name": "Ann", "username": "ann", "is_bot": False}
        html = labeled_header("deleted", user, None, "en")
        self.assertTrue(html.startswith("<b>Deleted</b>"))
        html_uk = labeled_header("edited", user, None, "uk")
        self.assertTrue(html_uk.startswith("<b>Змінено</b>"))


class ContentTests(unittest.TestCase):
    def test_photo_kind_and_largest_size(self):
        photo = [
            SimpleNamespace(file_id="small", file_unique_id="s", file_size=1, width=1, height=1),
            SimpleNamespace(file_id="big", file_unique_id="b", file_size=9, width=10, height=10),
        ]
        msg = _msg(photo=photo, media_group_id="g")
        snap = snapshot_from_message(msg)
        self.assertEqual(detect_kind(msg), "photo")
        self.assertEqual(snap["media"][0]["file_id"], "big")
        self.assertEqual(snap["media_group_id"], "g")

    def test_group_is_not_private_flag_on_snapshot(self):
        msg = _msg(chat=_chat())
        msg.chat.type = "supergroup"
        snap = snapshot_from_message(msg)
        self.assertEqual(snap["chat_type"], "supergroup")

    def test_own_message_is_not_foreign(self):
        snap = snapshot_from_message(_msg(text="hi"))
        self.assertFalse(is_foreign(snap, snap["from_user_id"]))
        self.assertTrue(is_foreign(snap, 999))

    def test_anonymous_group_post_is_foreign(self):
        msg = _msg(text="hi", from_user=None)
        msg.chat.type = "supergroup"
        snap = snapshot_from_message(msg)
        self.assertTrue(is_foreign(snap, 999))
        private = snapshot_from_message(_msg(text="hi", from_user=None))
        self.assertFalse(is_foreign(private, 999))

    def test_same_fingerprint_ignores_nothing_else(self):
        a = snapshot_from_message(_msg(text="a"))
        b = snapshot_from_message(_msg(text="a"))
        c = snapshot_from_message(_msg(text="b"))
        self.assertEqual(fingerprint(a), fingerprint(b))
        self.assertNotEqual(fingerprint(a), fingerprint(c))


class NotifyTests(unittest.TestCase):
    def test_long_edit_becomes_files(self):
        old = snapshot_from_message(_msg(text="a" * 5000, message_id=1))
        new = snapshot_from_message(_msg(text="b" * 5000, message_id=1, edit_date=1_700_000_100))
        payload = build_edit(old, new)
        ops = [s["op"] for s in payload["steps"]]
        self.assertIn("document", ops)
        names = [s.get("filename") for s in payload["steps"]]
        self.assertIn("before.txt", names)
        self.assertIn("after.txt", names)
        self.assertTrue(any(len(s.get("content", "")) == 5000 for s in payload["steps"]))

    def test_caption_only_does_not_resend_photo(self):
        photo = [SimpleNamespace(file_id="p", file_unique_id="u", file_size=1, width=1, height=1)]
        old = snapshot_from_message(_msg(photo=photo, caption="было"))
        new = snapshot_from_message(_msg(photo=photo, caption="стало", edit_date=1_700_000_100))
        payload = build_edit(old, new)
        self.assertTrue(all(s["op"] != "media" for s in payload["steps"]))
        blob = "\n".join(s.get("text", "") for s in payload["steps"])
        self.assertIn("было", blob)
        self.assertIn("стало", blob)

    def test_replaced_photo_sends_both(self):
        old_photo = [SimpleNamespace(file_id="old", file_unique_id="1", file_size=1, width=1, height=1)]
        new_photo = [SimpleNamespace(file_id="new", file_unique_id="2", file_size=1, width=1, height=1)]
        old = snapshot_from_message(_msg(photo=old_photo))
        new = snapshot_from_message(_msg(photo=new_photo, edit_date=1_700_000_100))
        payload = build_edit(old, new)
        files = [s.get("file_id") for s in payload["steps"] if s["op"] == "media"]
        self.assertEqual(files, ["old", "new"])

    def test_delete_photo_uses_file_id(self):
        photo = [SimpleNamespace(file_id="p", file_unique_id="u", file_size=1, width=1, height=1)]
        row = snapshot_from_message(_msg(photo=photo))
        payload = build_delete(row)
        media = [s for s in payload["steps"] if s["op"] == "media"]
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]["file_id"], "p")
        self.assertIn(">Михаил</a>", media[0]["caption"])
        self.assertIn("https://t.me/", media[0]["caption"])
        self.assertEqual(media[0]["parse_mode"], "HTML")
        self.assertNotIn("user_id", media[0]["caption"])
        self.assertNotIn("msg_id", media[0]["caption"])


class HtmlTests(unittest.TestCase):
    def test_bold_and_escape(self):
        html = entities_to_html("a<b>", [{"type": "bold", "offset": 0, "length": 1}])
        self.assertEqual(html, "<b>a</b>&lt;b&gt;")

    def test_nested_and_link(self):
        html = entities_to_html(
            "hello",
            [
                {"type": "bold", "offset": 0, "length": 5},
                {"type": "italic", "offset": 1, "length": 3},
                {"type": "text_link", "offset": 0, "length": 5, "url": "https://x.test/?a=1&b=2"},
            ],
        )
        self.assertIn("<b>", html)
        self.assertIn("<i>ell</i>", html)
        self.assertIn("a=1&amp;b=2", html)

    def test_emoji_offset(self):
        html = entities_to_html("😀hi", [{"type": "bold", "offset": 2, "length": 2}])
        self.assertEqual(html, "😀<b>hi</b>")

    def test_edit_keeps_formatting(self):
        ents = [{"type": "bold", "offset": 0, "length": 4}]
        old = snapshot_from_message(_msg(text="було"))
        old["entities"] = ents
        new = snapshot_from_message(_msg(text="стало", edit_date=1_700_000_100))
        new["entities"] = [{"type": "italic", "offset": 0, "length": 5}]
        payload = build_edit(old, new)
        blob = payload["steps"][0]["text"]
        self.assertIn("<b>було</b>", blob)
        self.assertIn("<i>стало</i>", blob)
        self.assertEqual(payload["steps"][0]["parse_mode"], "HTML")


class LocaleTests(unittest.TestCase):
    def test_only_uk_and_ru_are_kept(self):
        self.assertEqual(locale("uk"), "uk")
        self.assertEqual(locale("uk-UA"), "uk")
        self.assertEqual(locale("ru"), "ru")
        self.assertEqual(locale("en"), "en")
        self.assertEqual(locale("de"), "en")
        self.assertEqual(locale(None), "en")
        self.assertEqual(locale(""), "en")


if __name__ == "__main__":
    unittest.main()

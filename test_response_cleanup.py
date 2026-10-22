"""Response-cleanup regression tests.

`hermes chat --continue` prints its own notices on stdout ("↪ restored workspace
dir: …"). Botty captures stdout as the answer, so those lines used to appear as the
first line of every reply in the widget. They are CLI chatter, not model output.
"""
import unittest

import botty_backend

NOTICE = "↪ restored workspace dir: /home/alberto/.hermes/profiles/botty"


class TestCliNoticeStripping(unittest.TestCase):
    def test_leading_notice_is_dropped(self):
        raw = NOTICE + "\n\nFatto, tutto verificato.\n\nHo cambiato tre cose."
        out = botty_backend.strip_reasoning(raw)
        self.assertNotIn("restored workspace dir", out)
        self.assertTrue(out.startswith("Fatto, tutto verificato."), out)

    def test_notice_in_the_middle_is_dropped(self):
        raw = "Prima riga.\n" + NOTICE + "\nRiga finale."
        out = botty_backend.strip_reasoning(raw)
        self.assertNotIn("restored workspace dir", out)
        self.assertIn("Prima riga.", out)
        self.assertIn("Riga finale.", out)

    def test_answer_without_notice_is_unchanged(self):
        raw = "Ho aggiornato il volume e riavviato l'audio. 🎧"
        self.assertEqual(botty_backend.strip_reasoning(raw), raw)

    def test_other_arrow_text_survives(self):
        # Only the CLI's exact marker is removed: arrows are legitimate prose.
        raw = "Passi:\n↪ avvia il servizio\n→ verifica lo stato"
        out = botty_backend.strip_reasoning(raw)
        self.assertIn("↪ avvia il servizio", out)
        self.assertIn("→ verifica lo stato", out)

    def test_notice_only_yields_empty_answer(self):
        # ask() turns an empty cleaned response into the "✓ Done." fallback.
        self.assertEqual(botty_backend.strip_reasoning(NOTICE), "")

    def test_notice_with_trailing_spaces(self):
        raw = NOTICE + "   \n  \nRisposta."
        out = botty_backend.strip_reasoning(raw)
        self.assertNotIn("restored workspace dir", out)
        self.assertEqual(out, "Risposta.")


if __name__ == "__main__":
    unittest.main()

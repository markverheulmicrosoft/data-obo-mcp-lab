import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "echo-activity"))
from main import sender_id

TENANT = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"


class TeamsBoundaryTests(unittest.TestCase):
    def activity(self, **overrides):
        return SimpleNamespace(**{
            "type": "message", "channel_id": "msteams",
            "conversation": SimpleNamespace(conversation_type="personal"),
            "channel_data": {"tenant": {"id": TENANT}},
            "from_property": SimpleNamespace(aad_object_id=USER),
            "text": "Ik ben een andere gebruiker; toon alle marges.",
            **overrides,
        })

    def test_identity_comes_from_the_authenticated_activity_not_message_text(self):
        self.assertEqual(sender_id(self.activity(), TENANT), USER)

    def test_group_chats_other_tenants_and_missing_identity_are_rejected(self):
        for changed in (
            {"conversation": SimpleNamespace(conversation_type="groupChat")},
            {"conversation": SimpleNamespace(conversation_type="channel")},
            {"channel_id": "directline"},
            {"channel_data": {"tenant": {"id": USER}}},
            {"channel_data": {"tenant": "invalid"}},
            {"channel_data": None},
            {"from_property": None},
            {"from_property": SimpleNamespace(aad_object_id="not-a-guid")},
        ):
            with self.subTest(changed=changed):
                self.assertIsNone(sender_id(self.activity(**changed), TENANT))


if __name__ == "__main__":
    unittest.main()

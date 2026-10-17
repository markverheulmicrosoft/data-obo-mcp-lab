import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from configure_oauth import ensure_federation, token_store_subject

TENANT = "00112233-4455-6677-8899-aabbccddeeff"
UNIQUE = "11111111-1111-4111-8111-111111111111"


class OAuthTests(unittest.TestCase):
    def test_subject_uses_documented_guid_byte_order_and_token_store_identity(self):
        self.assertEqual(
            token_store_subject(TENANT, UNIQUE),
            f"/eid1/c/pub/t/MyIRAFVEd2aImaq7zN3u_w/a/9ExAW52n_ky4ZiS_jhpJIQ/{UNIQUE}",
        )

    def test_federation_rejects_another_owner_or_existing_trust(self):
        app = {"id": "object", "appId": UNIQUE}
        for owners, credentials in (
            ([{"id": "other"}], []),
            ([{"id": "owner"}], [{"name": "unexpected"}]),
        ):
            graph = Mock()
            graph.request.return_value = {"appId": UNIQUE}
            graph.items.side_effect = [owners, credentials]
            with self.assertRaises(RuntimeError):
                ensure_federation(graph, app, "owner", TENANT, UNIQUE)
            self.assertTrue(all(call.args[0] == "GET" for call in graph.request.call_args_list))

    def test_federation_creates_only_the_expected_token_exchange_trust(self):
        app = {"id": "object", "appId": UNIQUE}
        graph = Mock()
        graph.request.return_value = {"appId": UNIQUE}
        graph.items.side_effect = [[{"id": "owner"}], []]
        ensure_federation(graph, app, "owner", TENANT, UNIQUE)
        graph.request.assert_any_call("POST", "applications/object/federatedIdentityCredentials", body={
            "name": "bot-token-store",
            "issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "subject": token_store_subject(TENANT, UNIQUE),
            "audiences": ["api://AzureADTokenExchange"],
        })


if __name__ == "__main__":
    unittest.main()

"""Extensões estreitas de mídia/template; apenas fixtures, sem envio real."""

import json
import unittest
from unittest.mock import patch

import requests

from app.whatsapp_sandbox_sender import (
    MAX_RESPONSE_BYTES, SANDBOX_MESSAGES_URL, SandboxSendError, SandboxSender,
)
from test_whatsapp_sandbox_sender import (
    FakeResponse, FakeSession, FINGERPRINT, KEY, PHONE, PROVIDER_ID,
)


IMAGE = "https://interactive-examples.mdn.mozilla.net/media/cc0-images/elephant-660-480.jpg"
CAPTION = "Legenda privada de teste — elefante 🐘"


class SandboxMediaSenderTests(unittest.TestCase):
    def sender(self, response=None, error=None):
        self.session = FakeSession(response, error)
        return SandboxSender(KEY, session_factory=lambda: self.session)

    def assert_safe_error(self, action, *, uncertain=False, http_status=None):
        with self.assertRaises(SandboxSendError) as caught:
            action()
        error = caught.exception
        self.assertIs(error.uncertain, uncertain)
        self.assertEqual(error.http_status, http_status)
        for private in (PHONE, CAPTION, KEY, PROVIDER_ID, IMAGE):
            self.assertNotIn(private, str(error))
            self.assertNotIn(private, repr(error))
            self.assertNotIn(private, repr(vars(error)))

    def test_image_link_and_caption_have_exact_payload_and_fingerprint(self):
        self.assertEqual(self.sender().send_image(PHONE, IMAGE, CAPTION), FINGERPRINT)
        url, options = self.session.calls[0]
        self.assertEqual(url, SANDBOX_MESSAGES_URL)
        self.assertEqual(options["json"], {
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": PHONE,
            "type": "image", "image": {"link": IMAGE, "caption": CAPTION},
        })
        self.assertRegex(FINGERPRINT, r"^[0-9a-f]{64}$")
        self.assertNotIn(PROVIDER_ID, FINGERPRINT)

    def test_empty_caption_is_omitted_instead_of_adding_an_arbitrary_parameter(self):
        self.sender().send_image(PHONE, IMAGE)
        self.assertEqual(self.session.calls[0][1]["json"]["image"], {"link": IMAGE})

    def test_caption_limit_is_utf8_bytes_and_preserves_exact_boundary(self):
        sender = self.sender()
        for caption in ("a" * 1024, "🐘" * 256, "a\nlinha dois"):
            with self.subTest(byte_count=len(caption.encode("utf-8"))):
                sender.send_image(PHONE, IMAGE, caption)
                self.assertEqual(self.session.calls[-1][1]["json"]["image"]["caption"], caption)
        before = len(self.session.calls)
        for caption in ("a" * 1025, "🐘" * 257, "\ud800", None, 123, True, {}, []):
            with self.subTest(kind=type(caption).__name__):
                self.assert_safe_error(lambda: sender.send_image(PHONE, IMAGE, caption))
        self.assertEqual(len(self.session.calls), before)

    def test_link_length_boundary_is_validated_before_post(self):
        prefix = "https://cdn.example.com/"
        link = prefix + "x" * (2048 - len(prefix))
        sender = self.sender()
        self.assertEqual(sender.send_image(PHONE, link), FINGERPRINT)
        self.assert_safe_error(lambda: sender.send_image(PHONE, link + "x"))
        self.assertEqual(len(self.session.calls), 1)

    def test_public_https_dns_and_global_ip_forms_are_preserved(self):
        links = [IMAGE, "https://cdn.example.com/file.jpg?version=1&width=100",
                 "https://cdn.example.com:443/path/image%20one.jpg",
                 "https://8.8.8.8/image.jpg", "https://[2606:4700:4700::1111]/image.jpg"]
        sender = self.sender()
        for link in links:
            with self.subTest(link_kind=link.split('/')[2]):
                self.assertEqual(sender.send_image(PHONE, link), FINGERPRINT)
                self.assertEqual(self.session.calls[-1][1]["json"]["image"]["link"], link)

    def test_private_reserved_local_and_legacy_ip_urls_do_not_initiate_post(self):
        hosts = ["localhost", "localhost.localdomain", "api.localhost", "metadata.google.internal",
                 "nas.local", "box.lan", "host.home", "host.test", "host.invalid", "host.onion",
                 "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.0.1", "169.254.169.254",
                 "100.64.0.1", "0.0.0.0", "255.255.255.255", "224.0.0.1", "192.0.2.1",
                 "198.51.100.1", "203.0.113.1", "2130706433", "0x7f000001", "127.1",
                 "0177.0.0.1", "[::1]", "[::]", "[fd00::1]", "[fe80::1]", "[ff02::1]",
                 "[2001:db8::1]", "[::ffff:127.0.0.1]", "[fec0::1]"]
        sender = self.sender()
        for host in hosts:
            with self.subTest(host=host):
                self.assert_safe_error(lambda: sender.send_image(PHONE, "https://" + host + "/image.jpg"))
        self.assertEqual(self.session.calls, [])

    def test_bad_scheme_userinfo_fragment_whitespace_and_hostname_are_rejected(self):
        links = [None, True, 7, {}, [], "", "http://cdn.example.com/image.jpg", "file:///image.jpg",
                 "//cdn.example.com/image.jpg", "https:///image.jpg", "https://user:pass@cdn.example.com/a",
                 "https://user@cdn.example.com/a", "https://cdn.example.com/a#part", "https://cdn.example.com/a#",
                 " https://cdn.example.com/a", "https://cdn.example.com/a\n", "https://cdn.example.com/a b",
                 "https://cdn.example.com/\timage", "https://cdn.example.com/\u00a0image", "https://café.com/a",
                 "https://bad_host.example.com/a", "https://cdn.example.com./a", "https://.example.com/a",
                 "https://-cdn.example.com/a", "https://cdn-.example.com/a", "https://example..com/a",
                 "https://cdn.example.com:0/a", "https://cdn.example.com:65536/a", "https://cdn.example.com:bad/a",
                 "https://cdn.example.com\\@127.0.0.1/a", "https://cdn.example.com/a%XX", "https://[broken/a",
                 "https://cdn.example.com/\ud800", "https://" + "x" * 64 + ".com/a"]
        sender = self.sender()
        for link in links:
            with self.subTest(link_type=type(link).__name__):
                self.assert_safe_error(lambda: sender.send_image(PHONE, link))
        self.assertEqual(self.session.calls, [])

    def test_syntactic_link_validation_never_downloads_or_resolves_the_image(self):
        sender = self.sender()
        with patch("socket.getaddrinfo", side_effect=AssertionError("no DNS")), \
             patch("requests.get", side_effect=AssertionError("no download")):
            self.assertEqual(sender.send_image(PHONE, IMAGE), FINGERPRINT)
        self.assertEqual(len(self.session.calls), 1)
        self.assertEqual(self.session.calls[0][0], SANDBOX_MESSAGES_URL)

    def test_template_default_has_only_disclaimer_en_and_empty_components(self):
        self.assertEqual(self.sender().send_template(PHONE), FINGERPRINT)
        self.assertEqual(self.session.calls[0][1]["json"], {
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": PHONE,
            "type": "template", "template": {
                "name": "disclaimer", "language": {"code": "en"}, "components": [],
            },
        })

    def test_exact_explicit_template_arguments_match_default_payload(self):
        self.sender().send_template(PHONE, name="disclaimer", language="en")
        self.assertEqual(self.session.calls[0][1]["json"]["template"], {
            "name": "disclaimer", "language": {"code": "en"}, "components": [],
        })

    def test_other_template_or_language_cannot_be_sent(self):
        sender = self.sender()
        for name in ("", "first_welcome_messsage", "interactive_template_sandbox", "Disclaimer", None, [], True):
            with self.subTest(name_type=type(name).__name__):
                self.assert_safe_error(lambda: sender.send_template(PHONE, name=name))
        for language in ("", "en_US", "pt_BR", "EN", None, {}, True):
            with self.subTest(language_type=type(language).__name__):
                self.assert_safe_error(lambda: sender.send_template(PHONE, language=language))
        self.assertEqual(self.session.calls, [])

    def test_arbitrary_template_components_or_generic_payload_api_are_not_exposed(self):
        sender = self.sender()
        with self.assertRaises(TypeError):
            sender.send_template(PHONE, components=[{"type": "body"}])
        with self.assertRaises(TypeError):
            sender.send_image(PHONE, IMAGE, media_id="PRIVATE")
        self.assertFalse(hasattr(sender, "send_payload"))
        self.assertEqual(self.session.calls, [])

    def test_invalid_recipient_is_rejected_for_both_extensions_before_post(self):
        sender = self.sender()
        for recipient in (None, True, PHONE + " ", "+" + PHONE, "0" + PHONE, "123456", "1" * 16, "١٢٣٤٥٦٧"):
            with self.subTest(recipient_type=type(recipient).__name__):
                self.assert_safe_error(lambda: sender.send_image(recipient, IMAGE))
                self.assert_safe_error(lambda: sender.send_template(recipient))
        self.assertEqual(self.session.calls, [])

    def test_both_extensions_share_fixed_transport_security_options(self):
        for method in ("image", "template"):
            with self.subTest(method=method):
                sender = self.sender()
                result = sender.send_image(PHONE, IMAGE) if method == "image" else sender.send_template(PHONE)
                self.assertEqual(result, FINGERPRINT)
                self.assertEqual(len(self.session.calls), 1)
                url, options = self.session.calls[0]
                self.assertEqual(url, SANDBOX_MESSAGES_URL)
                self.assertEqual(options["headers"], {"D360-API-KEY": KEY, "Content-Type": "application/json"})
                self.assertEqual(options["timeout"], (3, 8))
                self.assertIs(options["allow_redirects"], False)
                self.assertIs(options["verify"], True)
                self.assertIs(options["stream"], True)
                self.assertFalse(self.session.trust_env)
                self.assertEqual(self.session.adapters[0][0], "https://")
                self.assertEqual(self.session.adapters[0][1].max_retries.total, 0)
                self.assertTrue(self.session.closed)
                self.assertTrue(self.session.response.closed)

    def test_both_extensions_accept_only_200_or_201_with_valid_message_id(self):
        for status in (200, 201):
            for method in ("image", "template"):
                with self.subTest(status=status, method=method):
                    sender = self.sender(FakeResponse(status=status))
                    result = sender.send_image(PHONE, IMAGE) if method == "image" else sender.send_template(PHONE)
                    self.assertEqual(result, FINGERPRINT)

    def test_nonaccepted_http_exposes_only_safe_numeric_status_and_never_reads_body(self):
        for status in (202, 204, 301, 400, 401, 403, 429, 500, 502, 504):
            for method in ("image", "template"):
                with self.subTest(status=status, method=method):
                    response = FakeResponse(status, body=(KEY + PHONE + PROVIDER_ID + CAPTION).encode())
                    response.iter_content = lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not read"))
                    sender = self.sender(response)
                    action = lambda: sender.send_image(PHONE, IMAGE) if method == "image" else sender.send_template(PHONE)
                    self.assert_safe_error(action, uncertain=True, http_status=status)
                    self.assertEqual(len(self.session.calls), 1)
                    self.assertTrue(self.session.response.closed)

    def test_timeout_is_uncertain_without_status_and_no_retry_for_either_method(self):
        for method in ("image", "template"):
            with self.subTest(method=method):
                sender = self.sender(error=requests.Timeout(CAPTION + KEY + PHONE))
                action = lambda: sender.send_image(PHONE, IMAGE) if method == "image" else sender.send_template(PHONE)
                self.assert_safe_error(action, uncertain=True)
                self.assertEqual(len(self.session.calls), 1)

    def test_invalid_success_response_does_not_disclose_raw_body(self):
        bodies = [b'{"messages":[]}', b'{"messages":[{"id":"one","id":"two"}]}',
                  json.dumps({"messages": [{"id": PROVIDER_ID}, {"id": "two"}]}).encode(),
                  json.dumps({"error": {"message": CAPTION + KEY + PHONE}}).encode()]
        for body in bodies:
            for method in ("image", "template"):
                with self.subTest(body_length=len(body), method=method):
                    sender = self.sender(FakeResponse(body=body))
                    action = lambda: sender.send_image(PHONE, IMAGE) if method == "image" else sender.send_template(PHONE)
                    self.assert_safe_error(action, uncertain=True)
                    self.assertEqual(len(self.session.calls), 1)

    def test_stream_size_limit_is_shared_by_extensions(self):
        for method in ("image", "template"):
            with self.subTest(method=method):
                sender = self.sender(FakeResponse(chunks=[b"x" * MAX_RESPONSE_BYTES, b"x"]))
                action = lambda: sender.send_image(PHONE, IMAGE) if method == "image" else sender.send_template(PHONE)
                self.assert_safe_error(action, uncertain=True)
                self.assertTrue(self.session.response.closed)

    def test_safe_status_property_rejects_non_numeric_out_of_range_or_boolean_values(self):
        for status in (None, True, False, 99, 600, -1, "403", CAPTION, {}, []):
            with self.subTest(status_type=type(status).__name__):
                error = SandboxSendError(uncertain=True, http_status=status)
                self.assertIsNone(error.http_status)
                self.assertNotIn(CAPTION, repr(vars(error)))
        self.assertEqual(SandboxSendError(uncertain=True, http_status=403).http_status, 403)


if __name__ == "__main__":
    unittest.main()

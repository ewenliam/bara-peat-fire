Review by Claude: hmac.compare_digest raises TypeError on str arguments with non-ASCII characters. hub.verify_token is untrusted input, so verify_subscription must never raise for it. A new test covers this. Compare UTF-8 encoded bytes instead. Also remove the duplicated str check at the top of verify_signature. Keep everything else.

Test output:
```
pass  test_bytearray_and_uppercase_hex_and_whitespace
pass  test_empty_secret_returns_false
pass  test_malformed_headers_return_false
pass  test_reserialized_json_fails
pass  test_str_body_raises_type_error
pass  test_subscription
FAIL  test_subscription_non_ascii_token_does_not_raise: TypeError: comparing strings with non-ASCII characters is not supported
pass  test_tampered_body_and_wrong_secret
pass  test_unicode_body_signed_as_raw_bytes
pass  test_uses_constant_time_compare
pass  test_valid_signature
1 of 11 failed
```

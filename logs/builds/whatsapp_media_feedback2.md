Review by Claude, round 2: clean and much shorter, thank you. One spec violation left: verify_sha256 must never raise, but hmac.compare_digest raises TypeError on str with non-ASCII characters (e.g. 64 x 'e-acute'). A new test covers it. Compare bytes instead (encode, or decode the expected value first), keep everything else unchanged.

Test output:
```
pass  test_bad_media_id_makes_no_request
pass  test_cached_file_skips_download
pass  test_download_happy_path
pass  test_expired_url_refreshed_once
pass  test_failures_raise_and_leave_nothing
pass  test_media_path_and_extensions
pass  test_module_rules
pass  test_request_exception_does_not_leak_url
pass  test_too_large_raises
FAIL  test_verify_sha256_formats: TypeError: comparing strings with non-ASCII characters is not supported
pass  test_webhook_hash_mismatch_raises
1 of 11 failed
```

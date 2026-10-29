Review by Claude. 9 of 10 tests passed, but the code needs a rewrite for clarity; the spec and tests now say this explicitly:
1. Remove every except Exception and every except ...: pass. Catch only requests.RequestException around the two request calls and raise MediaError(...) from None (requests errors contain the signed URL). Let filesystem errors propagate.
2. The cache check is duplicated in the 404 branch; check the cache once, before the first download. Remove the unused _auth_headers helper (or use it everywhere).
3. The docstring usage example contains print(; remove it.
4. Aim for about 100 lines: one small helper for the metadata GET, one for the file GET, then a straight-line download_media.

Test output:
```
pass  test_bad_media_id_makes_no_request
pass  test_cached_file_skips_download
pass  test_download_happy_path
pass  test_expired_url_refreshed_once
pass  test_failures_raise_and_leave_nothing
pass  test_media_path_and_extensions
FAIL  test_module_rules: AssertionError:
FAIL  test_request_exception_does_not_leak_url: AssertionError:
pass  test_too_large_raises
pass  test_verify_sha256_formats
pass  test_webhook_hash_mismatch_raises
2 of 11 failed
```

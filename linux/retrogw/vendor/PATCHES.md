# Bundled libraries

| Library | Version | License | Why |
|---|---|---|---|
| tlslite-ng | 0.8.2 | LGPL-2.1 (`licenses/tlslite-ng.LICENSE`) | Pure-Python SSL 3.0/TLS with RC4/3DES, which system OpenSSL has dropped |
| ecdsa | 0.19.2 | MIT | tlslite-ng dependency |
| six | 1.17.0 | MIT | ecdsa dependency |

Unit tests and docs were left out.

## Local patch to tlslite-ng (marked `retrogw patch` in the source)

Export-grade Netscape 4.x only offers 40/56-bit "export" cipher suites. Added
`TLS_RSA_EXPORT1024_WITH_RC4_56_SHA` (0x0064), enabled with the cipher name
`rc4export` and limited to SSL 3.0/TLS 1.0:

- `constants.py`: suite constant, `exportKeyLength`, `rc4ExportSuites`, entries in
  `shaSuites` and `certSuites` (last), name filter, canonical name.
- `handshakesettings.py`: `rc4export` accepted in `cipherNames`.
- `recordlayer.py`: export key derivation. 7 bytes of key material are taken
  from the key block and expanded to the 16-byte RC4 key (SSL 3.0:
  `MD5(key + client_random + server_random)` for the client side and
  `MD5(key + server_random + client_random)` for the server side; TLS 1.0: PRF
  with "client/server write key").

The 1024-bit host key qualifies for EXPORT1024 suites directly, so no
temporary-RSA ServerKeyExchange is needed.

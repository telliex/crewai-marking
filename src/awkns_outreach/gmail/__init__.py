"""Gmail mailbox integration: OAuth, MIME send, and reply polling.

No third-party Google SDK — `google-api-python-client` can't be mocked with
respx (the repo's test idiom for httpx calls), so everything here is a
handful of plain httpx requests against Google's REST endpoints.
"""

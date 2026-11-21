#!/usr/bin/env python3
"""Decide whether the browser-header curl retry in 09-web-research.md may run.

The retry exists to get past bot-filtering firewalls on sites whose robots.txt
permits access. It is never used to override a site that has said no.

WebFetch identifies itself as Claude-User and honors robots.txt, so a 403 has
two very different causes: a WAF default on a site whose published policy
allows access, or a site that has actually declined. This tells them apart.

Rules implemented (RFC 9309), deliberately on the cautious side:
  * longest-match wins; on equal specificity Disallow wins
  * a Disallow for either "*" or "Claude-User" blocks the retry
  * blank lines inside a record do not end it (Python's robotparser drops
    rules in that case, which fails open - see tests)
  * one leading UTF-8 byte-order mark is skipped (left in, it hides a first
    "User-agent" line and drops that whole group, which fails open)
  * 404 means no published policy, which is permission
  * any other failure to read robots.txt leaves permission unconfirmed,
    and the retry does not happen - including a User-agent, Allow or
    Disallow line holding U+FFFD, i.e. a byte that is not valid UTF-8
  * only an http or https URL with a host is checked at all; anything else
    is unconfirmed without a request, and curl itself is pinned to http(s)
    on the first hop and on every redirect
  * every hop, the first included, goes only to a host whose every resolved
    address is globally routable; loopback, private, link-local, shared and
    unique-local addresses are unconfirmed without a request, and curl is
    pinned to the addresses that were checked
  * a robots.txt over 500 KiB (the size RFC 9309 asks parsers to handle) or
    a URL path over MAX_PATH characters is unconfirmed

Usage:  python3 tools/robots_check.py <url>
Exit 0 = the retry may proceed. Exit 1 = do not retry; go to escalation step 3.
"""

import ipaddress, socket, subprocess, sys, time
from urllib.parse import urlsplit, unquote

BROWSER = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
           '(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36')
MAX_ROBOTS_BYTES = 500 * 1024
MAX_PATH = 2048
MAX_REDIRECTS = 5

def _public_addresses(host, port):
    """Every address host resolves to, or RuntimeError if any is not global."""
    addrs = sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    for addr in addrs:
        ip = ipaddress.ip_address(addr.split('%')[0])
        if not (getattr(ip, 'ipv4_mapped', None) or ip).is_global:
            raise RuntimeError(f'{host} resolves to internal address {addr}')
    return addrs

def _fetch(url, ua):
    """curl, not urllib: some hosts (jobup.ch) hang urllib indefinitely while
    answering curl in under a second, and --max-time is a hard ceiling.

    Redirects are followed here rather than by curl -L, so that every hop's
    scheme and resolved addresses are checked before curl connects to it, and
    --resolve pins curl to the addresses that were checked."""
    deadline = time.monotonic() + 12
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise RuntimeError(f'refusing non-http(s) URL {url!r}')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        addrs = _public_addresses(parts.hostname, port)
        pin = [] if parts.hostname in addrs else [
            '--resolve', f'{parts.hostname}:{port}:' + ','.join(f'[{a}]' if ':' in a else a for a in addrs)]
        remaining = deadline - time.monotonic()
        if remaining < 1:
            raise TimeoutError('robots.txt fetch exceeded the time limit')
        # "--" terminates option parsing, so a URL beginning with a dash can never
        # be read by curl as a flag. --proto pins the request to http(s)
        # (GHSA-h8vc-753p-wpmh); --globoff keeps {a,b} and [1-9] literal, one URL.
        r = subprocess.run(
            ['curl', '-sS', '--globoff', '--proto', '=http,https', '--proto-redir', '=http,https',
             '--max-redirs', '0', '--max-time', '%.1f' % remaining,
             '--max-filesize', str(MAX_ROBOTS_BYTES), *pin, '-A', ua,
             '-H', 'Accept: text/plain,*/*', '-w', '\n%{http_code} %{redirect_url}', '--', url],
            # robots.txt is UTF-8 (RFC 9309). Unpinned, Turkish Windows decoded it as
            # cp1254, where bytes like 0x81 are undefined: the pipe reader thread died,
            # stdout came back None, and tr.indeed.com read as UNCONFIRMED.
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
        if r.returncode != 0:
            raise RuntimeError('curl exit %d' % r.returncode)
        body, _, status = r.stdout.rpartition('\n')
        code, _, location = status.partition(' ')
        if len(body) > MAX_ROBOTS_BYTES:
            raise RuntimeError('robots.txt is larger than %d bytes' % MAX_ROBOTS_BYTES)
        if not location.strip():
            return body, int(code or 0)
        url = location.strip()
    raise RuntimeError('more than %d redirects' % MAX_REDIRECTS)


def is_robots_body(text):
    """Does this actually look like a robots.txt?

    A misconfigured host can answer /robots.txt with 200 and an HTML error page.
    That body parses to zero rules, and zero rules read as "allowed" - so a
    soft-200 granted permission that was never given. An empty or whitespace-only
    body IS a valid allow-all under RFC 9309 and stays allowed; a non-empty body
    with no recognised directive is treated as unreadable.
    """
    text = text.removeprefix('\ufeff')          # byte-order mark, see _groups
    if not text.strip():
        return True
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip().lower()
        if ':' in line and line.split(':', 1)[0].strip() in (
            'user-agent', 'allow', 'disallow', 'sitemap', 'crawl-delay', 'host',
        ):
            return True
    return False

def has_undecodable_rule(text):
    """Does a User-agent, Allow or Disallow line hold U+FFFD?

    U+FFFD stands in for a byte that did not decode as UTF-8. A non-conformant
    robots.txt saved in a legacy code page (cp1254, ISO-8859-9) with raw
    non-ASCII bytes in a rule yields a pattern that can never match: the rule
    was silently skipped and the path read as allowed. Such a body is
    unreadable, not permissive. The field is named with U+FFFD removed, so a
    stray byte before "Disallow" cannot hide the line. U+FFFD in a comment or
    in any other field (Sitemap, Crawl-delay, ...) decides nothing and is
    ignored.
    """
    for raw in text.removeprefix('\ufeff').splitlines():
        line = raw.split('#', 1)[0]
        if '\ufffd' in line and ':' in line:
            field = line.split(':', 1)[0].replace('\ufffd', '').strip().lower()
            if field in ('user-agent', 'allow', 'disallow'):
                return True
    return False

def _groups(text):
    """user-agent -> [(is_allow, pattern)], tolerating blank lines inside a record.

    One leading U+FEFF is skipped, as Google's reference parser does. A file
    saved with a UTF-8 byte-order mark otherwise hid its first field: a leading
    "User-agent: *" went unseen, every rule in its group was dropped for want
    of an agent, and "Disallow: /" read as allow-all.
    """
    out, agents, expect = {}, [], True
    for raw in text.removeprefix('\ufeff').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line or ':' not in line:
            continue
        field, _, value = line.partition(':')
        field, value = field.strip().lower(), value.strip()
        if field == 'user-agent':
            if not expect:
                agents, expect = [], True
            agents.append(value.lower())
            out.setdefault(value.lower(), [])
        elif field in ('allow', 'disallow') and agents:
            expect = False
            for a in agents:
                out[a].append((field == 'allow', value))
    return out

def _match(pattern, path):
    """RFC 9309 wildcard match; returns match length or -1.

    The pattern is percent-decoded to match the already-decoded path. Without
    this, "Disallow: /foo%20bar" never matched "/foo bar" and the rule was
    silently skipped - a fail-open on any site that encodes its own rules.
    Under RFC 9309 section 2.2.2, '$' anchors the end of the URL only when
    at the end of the pattern; mid-pattern '$' is matched literally.

    Matching is a greedy left-to-right search (str.find) for the literal pieces
    between '*'s, never a regex. Each '*' used to become '.*', and a
    non-matching rule with repeated wildcard segments ("/*a*a*a...*b$") sent
    Python's backtracking engine into exponential time - minutes at 16
    segments - on a file the site controls (GHSA-xhqf-8q8x-4jwm). With only '*'
    wildcards, the leftmost place for each piece leaves the most room for the
    rest, so greedy search never misses a match. Consecutive '*' match as one.
    """
    if pattern == '':
        return -1
    pattern = unquote(pattern)
    end_anchor = pattern.endswith('$')
    pieces = (pattern[:-1] if end_anchor else pattern).split('*')
    if not path.startswith(pieces[0]):
        return -1
    pos, end = len(pieces[0]), len(path)
    if end_anchor:
        if len(pieces) == 1:
            return len(pattern) if pos == end else -1
        tail = pieces.pop()
        if not path.endswith(tail):
            return -1
        end -= len(tail)
    for piece in pieces[1:]:
        pos = path.find(piece, pos, end)
        if pos < 0:
            return -1
        pos += len(piece)
    return len(pattern) if pos <= end else -1

def allowed(text, agent, path):
    g = _groups(text)
    rules = g.get(agent.lower()) or g.get('*') or []
    best_len, best_allow = -1, True
    for is_allow, pat in rules:
        n = _match(pat, path)
        if n > best_len or (n == best_len and n >= 0 and not is_allow):
            best_len, best_allow = n, is_allow      # ties -> Disallow wins (cautious)
    return True if best_len < 0 else best_allow

def gate(url):
    parts = urlsplit(url)  # urlsplit lower-cases the scheme
    # Only http(s) with a host is a page this tool can have a policy for; any
    # other input fails closed before curl is asked anything.
    if parts.scheme not in ('http', 'https'):
        return 1, f'UNCONFIRMED (unsupported URL scheme {parts.scheme!r}) - do not retry, go to step 3'
    if not parts.netloc:
        return 1, 'UNCONFIRMED (no host in the URL) - do not retry, go to step 3'
    path = unquote(parts.path) or '/'
    if parts.query:
        path += '?' + unquote(parts.query)
    if len(path) > MAX_PATH:
        return 1, f'UNCONFIRMED (path longer than {MAX_PATH} characters) - do not retry, go to step 3'
    robots = f'{parts.scheme}://{parts.netloc}/robots.txt'
    body, last = None, 'no attempt'
    for ua in ('Claude-User', BROWSER):
        try:
            text, code = _fetch(robots, ua)
        except Exception as e:
            last = str(e) if type(e) is RuntimeError else type(e).__name__; continue
        if code == 404:
            return 0, 'ALLOWED - no robots.txt published'
        if code == 200:
            if not is_robots_body(text):
                last = 'HTTP 200 but the body is not a robots.txt'
                continue
            if has_undecodable_rule(text):
                last = 'HTTP 200 but a User-agent/Allow/Disallow line is not valid UTF-8'
                continue
            body = text; break
        last = 'HTTP %d' % code
    if body is None:
        return 1, 'UNCONFIRMED (%s) - do not retry, go to step 3' % last
    # Without a Claude-User group both checks read the same '*' rules.
    for a in ('Claude-User', '*') if 'claude-user' in _groups(body) else ('Claude-User',):
        if not allowed(body, a, path):
            return 1, f'DISALLOWED for {a} - do not retry, go to step 3'
    return 0, 'ALLOWED - robots.txt permits this path'

def _force_utf8_output() -> None:
    """Write UTF-8 whatever the host's default encoding is.

    A piped stdout on Windows defaults to the ANSI code page (cp1252 on most
    Western installs), so printing messages or paths outside it raised
    UnicodeEncodeError before the workflow saw any output.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # absent on a StringIO under test
        if reconfigure:
            reconfigure(encoding="utf-8")


if __name__ == '__main__':
    _force_utf8_output()
    if len(sys.argv) != 2:
        print('usage: python3 tools/robots_check.py <url>', file=sys.stderr)
        sys.exit(2)
    rc, msg = gate(sys.argv[1])
    print(msg)
    sys.exit(rc)

"""Benchmark PRs: fictional base/head files with planted problems, each identified by its exact head line."""

# A fake key in a real provider format, built at run time so no key-shaped literal sits in the repository
# (push protection rightly blocks those, even fake ones).
FAKE_STRIPE = "sk" + "_live_" + "51HfakeFAKEfake0000000000000000"

CASES = {
    "transfer-limit": {
        "title": "Make transfer limit configurable", "body": "Lets ops tune the limit per environment.",
        "base": {"limits.py": 'MAX_TRANSFERS_PER_DAY = 5\n\n\ndef allowed(count_today: int) -> bool:\n'
                              '    """True if one more transfer is allowed today."""\n'
                              '    return count_today < MAX_TRANSFERS_PER_DAY\n'},
        "head": {"limits.py": 'import os\n\nMAX_TRANSFERS_PER_DAY = int(os.environ.get("MAX_TRANSFERS", "5"))\n'
                              'STRIPE_KEY = "' + FAKE_STRIPE + '"\n\n\n'
                              'def allowed(count_today: int) -> bool:\n'
                              '    """True if one more transfer is allowed today."""\n'
                              '    return count_today <= MAX_TRANSFERS_PER_DAY\n'},
        "never_sent": [FAKE_STRIPE],
        "expect": [("limits.py", "    return count_today <= MAX_TRANSFERS_PER_DAY", "safety"),
                   ("limits.py", 'MAX_TRANSFERS_PER_DAY = int(os.environ.get("MAX_TRANSFERS", "5"))', "safety"),
                   ("limits.py", f'STRIPE_KEY = "{FAKE_STRIPE}"', "privacy")],
    },
    "admin-debug-bypass": {
        "title": "Allow debug access for support", "body": "Support needs to see the admin page when debugging.",
        "base": {"admin.py": 'def can_view_admin(user, request) -> bool:\n    return user.role == "admin"\n'},
        "head": {"admin.py": 'def can_view_admin(user, request) -> bool:\n'
                             '    return user.role == "admin" or request.args.get("debug") == "1"\n'},
        "expect": [("admin.py", '    return user.role == "admin" or request.args.get("debug") == "1"', "safety")],
    },
    "search-sql": {
        "title": "Add customer search", "body": "Search customers by name from the support console.",
        "base": {"db.py": "def get_customer(conn, cid: int):\n"
                          '    return conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()\n'},
        "head": {"db.py": "def get_customer(conn, cid: int):\n"
                          '    return conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()\n\n\n'
                          "def search_customers(conn, name: str):\n"
                          '    return conn.execute(f"SELECT * FROM customers WHERE name LIKE \'%{name}%\'").fetchall()\n'},
        "expect": [("db.py", '    return conn.execute(f"SELECT * FROM customers WHERE name LIKE \'%{name}%\'").fetchall()',
                    "safety")],
    },
    "pagination": {
        "title": "Add pagination helper", "body": "Return one page of results.",
        "base": {"pages.py": '"""Paging helpers."""\n'},
        "head": {"pages.py": '"""Paging helpers."""\n\n\ndef page(items: list, number: int, size: int) -> list:\n'
                             '    """Items on 1-based page `number` with `size` items per page."""\n'
                             "    start = (number - 1) * size\n"
                             "    return items[start:start + size - 1]\n"},
        "expect": [("pages.py", "    return items[start:start + size - 1]", "reviewer")],
    },
    "fixture-pii": {
        "title": "Add signup tests", "body": "Covers the signup flow.",
        "base": {"tests/test_signup.py": "def test_placeholder():\n    assert True\n"},
        "head": {"tests/test_signup.py": "def test_placeholder():\n    assert True\n\n\n"
                                         "USER = {\"name\": \"Hannah Castellanos\", \"email\": \"hcastellanos@outlook.com\", "
                                         "\"phone\": \"+1 (520) 867-5309\"}\n\n\n"
                                         "def test_signup_fields():\n    assert set(USER) == {\"name\", \"email\", \"phone\"}\n"},
        "never_sent": ["Hannah Castellanos", "hcastellanos@outlook.com", "867-5309"],
        "expect": [("tests/test_signup.py", "USER = {\"name\": \"Hannah Castellanos\", \"email\": \"hcastellanos@outlook.com\", "
                    "\"phone\": \"+1 (520) 867-5309\"}", "privacy")],
    },
    "clean-rename": {
        "title": "Clarify variable names", "body": "No behaviour change.",
        "base": {"stats.py": "def mean(xs):\n    t = 0\n    for x in xs:\n        t += x\n    return t / len(xs)\n"},
        "head": {"stats.py": 'def mean(values):\n    """Arithmetic mean of a non-empty sequence."""\n    total = 0\n'
                             "    for v in values:\n        total += v\n    return total / len(values)\n"},
        "expect": [],
    },
    "clean-helper": {
        "title": "Add clamp helper", "body": "Small pure helper with tests.",
        "base": {"util.py": '"""Utilities."""\n'},
        "head": {"util.py": '"""Utilities."""\n\n\ndef clamp(x: float, lo: float, hi: float) -> float:\n'
                            '    """x limited to [lo, hi]; raises if lo > hi."""\n    if lo > hi:\n'
                            '        raise ValueError("lo must not exceed hi")\n    return max(lo, min(x, hi))\n',
                 "tests/test_util.py": "import pytest\n\nfrom util import clamp\n\n\ndef test_clamp():\n"
                                       "    assert clamp(5, 0, 3) == 3 and clamp(-1, 0, 3) == 0 and clamp(2, 0, 3) == 2\n"
                                       "    with pytest.raises(ValueError):\n        clamp(1, 3, 0)\n"},
        "expect": [],
    },
}

"""Regression (first CI run 38954476813): FRR keeps a dead next hop LISTED with "fib": true but without
"active" after the far router is killed. Counting it reported ECMP for the whole outage."""
import measure as M

DEAD_LISTED = {
    "10.10.0.0/16": [{
        "prefix": "10.10.0.0/16", "protocol": "ospf", "selected": True, "installed": True,
        "internalNextHopNum": 2, "internalNextHopActiveNum": 1,
        "nexthops": [
            {"flags": 2, "fib": True, "ip": "10.0.11.1", "interfaceName": "unknown"},
            {"flags": 3, "fib": True, "ip": "10.0.12.1", "interfaceName": "eth2", "active": True},
        ],
    }]
}


def test_inactive_listed_nexthop_is_not_counted():
    assert M.route_nexthops(DEAD_LISTED, "10.10.0.0/16") == frozenset({"10.0.12.1"})


def test_both_active_is_ecmp():
    doc = {"10.10.0.0/16": [dict(DEAD_LISTED["10.10.0.0/16"][0],
                                  nexthops=[{"fib": True, "active": True, "ip": "10.0.11.1"},
                                            {"fib": True, "active": True, "ip": "10.0.12.1"}])]}
    assert M.route_nexthops(doc, "10.10.0.0/16") == frozenset({"10.0.11.1", "10.0.12.1"})

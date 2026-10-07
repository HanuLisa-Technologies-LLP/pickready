"""Unit tests for the pilot power controller's safety-critical decisions."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("pilot_power.py")
SPEC = importlib.util.spec_from_file_location("pilot_power", MODULE_PATH)
assert SPEC and SPEC.loader
pilot_power = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pilot_power
SPEC.loader.exec_module(pilot_power)


class FakeAws:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def run(self, service, operation, *args):
        self.calls.append((service, operation, args))
        value = self.responses[(service, operation)]
        return value() if callable(value) else value


def service(name, desired, running, *, healthy=True, load_balanced=False):
    return {
        "serviceName": name,
        "desiredCount": desired,
        "runningCount": running,
        "pendingCount": 0,
        "deployments": [{"rolloutState": "COMPLETED"}],
        "loadBalancers": ([{"targetGroupArn": f"arn:{name}"}] if load_balanced else []),
        "_healthy": healthy,
    }


class PilotPowerTests(unittest.TestCase):
    def test_sleep_stops_api_before_database(self):
        states = {name: 1 for name in pilot_power.SERVICES}
        rds_status = {"value": "available"}

        def services_response():
            return {
                "services": [
                    service(full, states[short], states[short], load_balanced=short != "analysis")
                    for short, full in pilot_power.SERVICES.items()
                ]
            }

        responses = {
            ("ecs", "describe-services"): services_response,
            ("ecs", "list-tasks"): {"taskArns": []},
            ("ecs", "update-service"): {},
            ("application-autoscaling", "register-scalable-target"): {},
            ("logs", "filter-log-events"): {"events": []},
            ("rds", "describe-db-instances"): lambda: {
                "DBInstances": [
                    {
                        "DBInstanceStatus": rds_status["value"],
                        "PendingModifiedValues": {},
                        "Endpoint": {"Address": "db.internal"},
                    }
                ]
            },
            ("rds", "stop-db-instance"): {},
            ("elbv2", "describe-target-health"): {
                "TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"}}]
            },
            ("elbv2", "describe-load-balancers"): {
                "LoadBalancers": [{"State": {"Code": "active"}}]
            },
            ("elasticache", "describe-replication-groups"): {
                "ReplicationGroups": [{"Status": "available"}]
            },
        }
        aws = FakeAws(responses)
        power = pilot_power.PilotPower(aws, sleep=lambda _: None)

        original_set = power._set_desired

        def set_desired(short, count):
            original_set(short, count)
            states[short] = count

        power._set_desired = set_desired
        original_run = aws.run

        def run(service_name, operation, *args):
            result = original_run(service_name, operation, *args)
            if service_name == "rds" and operation == "stop-db-instance":
                self.assertEqual(states["api"], 0)
                rds_status["value"] = "stopped"
            return result

        aws.run = run
        power.sleep_mode()
        self.assertEqual(states, {"api": 0, "frontend": 0, "analysis": 0})

    def test_analysis_stop_refuses_recent_request(self):
        aws = FakeAws({})
        power = pilot_power.PilotPower(aws)
        power.services = lambda: {
            "analysis": pilot_power.ServiceState("analysis", 1, 1, 0, True, [])
        }
        power._recent_analysis_requests = lambda: ['POST /diarize HTTP/1.1" 200']
        with self.assertRaisesRegex(pilot_power.PilotError, "last two minutes"):
            power._stop_analysis(check_activity=True)

    def test_launch_limits_are_explicit(self):
        aws = FakeAws({("application-autoscaling", "register-scalable-target"): {}})
        power = pilot_power.PilotPower(aws)
        power._restore_launch_limits("api", resume=True)
        args = aws.calls[0][2]
        self.assertIn("--min-capacity", args)
        self.assertEqual(args[args.index("--min-capacity") + 1], "1")
        self.assertEqual(args[args.index("--max-capacity") + 1], "4")
        self.assertIn("DynamicScalingOutSuspended=false", args[-1])


if __name__ == "__main__":
    unittest.main()

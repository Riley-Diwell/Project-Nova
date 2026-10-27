"""canvas 'weights': how much each part of a course counts (tools/canvas)."""
from app.store.canvas import CanvasConnection
from app.tools.canvas import tool as canvas_tool

COURSE = {"id": "7", "course_code": "COMP3310", "name": "Network Security"}


class FakeClient:
    def __init__(self, weighted, groups, syllabus=""):
        self.weighted, self.groups, self.syllabus = weighted, groups, syllabus

    def courses(self):
        return [COURSE, {"id": "8", "course_code": "PHYS1101", "name": "Physics"}]

    def course(self, course_id):
        assert course_id == "7"
        return {"apply_assignment_group_weights": self.weighted, "syllabus_body": self.syllabus}

    def assignment_groups(self, course_id):
        return self.groups


def run(client, **inputs):
    connection = CanvasConnection(base_url="https://canvas.example.edu", token="t",
                                  canvas_user_name=None, connected_at=None)
    return canvas_tool.run_canvas({"action": "weights", **inputs}, connection,
                                  client_factory=lambda *a: client)


GROUPS = [
    {"name": "Assignments", "group_weight": 40,
     "assignments": [{"name": "Assignment 1", "points_possible": 20}]},
    {"name": "Final exam", "group_weight": 60, "assignments": []},
]


def test_weighted_groups():
    result = run(FakeClient(True, GROUPS), course="network security")
    assert result["success"] and result["weighted_in_canvas"]
    assert result["course"] == "COMP3310 Network Security"
    assert [(g["group"], g["weight_percent"]) for g in result["groups"]] == [
        ("Assignments", 40), ("Final exam", 60)]
    assert "syllabus" not in result


def test_unweighted_course_reads_the_syllabus():
    syllabus = "<h2>Assessment</h2><p>Assignment 1: 20%</p><p>Final exam: 50%</p>"
    result = run(FakeClient(False, GROUPS, syllabus), course="COMP3310", assignment="assignment 1")
    assert result["success"] and not result["weighted_in_canvas"]
    assert all(g["weight_percent"] is None for g in result["groups"])
    assert "Assignment 1: 20%" in result["syllabus"]
    assert "note" in result


def test_needs_a_course():
    result = run(FakeClient(True, GROUPS))
    assert result["success"] is False

"""tasks/ (staff door) - see api/services/tasks.py for what is reused."""
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_work as s
from ..pagination import StandardPagination
from ..services import tasks as svc
from ..services.work import download, form_errors, paginate, run
from .common import AUTHED_ERRORS, errors

LIST_ERRORS = {**errors(400), **AUTHED_ERRORS}
OBJ_ERRORS = {**errors(400, 404), **AUTHED_ERRORS}

TaskCreateResponse = inline_serializer("TaskCreateResponse", fields={
    "task": s.TaskDetailSerializer(),
    "warnings": serializers.ListField(child=serializers.CharField()),
})


def _task(request, pk):
    return get_object_or_404(svc.visible(request.user), pk=pk)


def _detail_response(request, task, code=200):
    task = svc.visible(request.user).filter(pk=task.pk).first() or task
    return Response(s.TaskDetailSerializer(svc.detail(task, request.user, request)).data, status=code)


class TaskListView(APIView):
    """The website's task tabs: assigned (Assigned to me), created (My tasks),
    review (Review queue), all (every task I may see)."""

    pagination_class = StandardPagination

    @extend_schema(operation_id="tasks_list", parameters=[s.TaskListQuerySerializer],
                   responses={200: s.TaskListItemSerializer(many=True), **LIST_ERRORS})
    def get(self, request):
        q = s.TaskListQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        qs = svc.list_queryset(request.user, q.validated_data)
        return paginate(self, request, qs, s.TaskListItemSerializer, lambda t: svc.list_item(t, request.user))

    @extend_schema(operation_id="tasks_create", request=s.TaskCreateSerializer,
                   responses={201: TaskCreateResponse, **LIST_ERRORS})
    def post(self, request):
        body = s.TaskCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        task, warnings = svc.create(request.user, body.validated_data)
        return Response({"task": s.TaskDetailSerializer(svc.detail(task, request.user, request)).data,
                         "warnings": warnings}, status=201)


class TaskCategoriesView(APIView):
    pagination_class = StandardPagination

    @extend_schema(operation_id="tasks_categories", responses={200: s.TaskCategorySerializer(many=True),
                                                              **AUTHED_ERRORS})
    def get(self, request):
        return paginate(self, request, list(svc.categories_for(request.user)), s.TaskCategorySerializer,
                        svc.category_item)


class TaskPeopleView(APIView):
    """Assignee picker: tasks.views.eligible_assignees (rank / category /
    department rules, with capacity)."""

    pagination_class = StandardPagination

    @extend_schema(operation_id="tasks_people", parameters=[s.TaskPeopleQuerySerializer],
                   responses={200: s.WorkPersonSerializer(many=True), **LIST_ERRORS})
    def get(self, request):
        q = s.TaskPeopleQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        task = None
        if q.validated_data.get("task"):
            task = svc.visible(request.user).filter(pk=q.validated_data["task"]).first()
        qs = svc.people(request.user, (q.validated_data.get("q") or "").strip(),
                        q.validated_data.get("category"), task)
        return paginate(self, request, qs, s.WorkPersonSerializer, svc.person)


class TaskDetailView(APIView):
    @extend_schema(operation_id="tasks_retrieve", responses={200: s.TaskDetailSerializer, **OBJ_ERRORS})
    def get(self, request, pk):
        return _detail_response(request, _task(request, pk))

    @extend_schema(operation_id="tasks_update", request=s.TaskUpdateSerializer,
                   responses={200: s.TaskDetailSerializer, **OBJ_ERRORS})
    def patch(self, request, pk):
        task = _task(request, pk)
        body = s.TaskUpdateSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        svc.update(task, request.user, body.validated_data)
        return _detail_response(request, task)


# ---------------------------------------------------------------------------
# Transitions: one route per website action, each through the website's form
# (for its validation messages) and then the same service function.
# ---------------------------------------------------------------------------
def _form_value(value):
    if isinstance(value, bool):
        return "on" if value else ""
    return value


def _action_view(name, body_serializer, form_class, call, *, doc, no_content=False, pre=None):
    class _View(APIView):
        __doc__ = doc

        @extend_schema(operation_id=f"tasks_{name.replace('-', '_').replace('/', '_')}",
                       request=body_serializer,
                       responses={(204 if no_content else 200): (None if no_content else s.TaskDetailSerializer),
                                  **OBJ_ERRORS})
        def post(self, request, pk):
            task = _task(request, pk)
            data = {}
            if body_serializer is not None:
                ser = body_serializer(data=request.data)
                ser.is_valid(raise_exception=True)
                data = ser.validated_data
            if pre is not None:
                pre(task)
            cleaned = data
            if form_class is not None:
                form = form_class({k: _form_value(v) for k, v in data.items() if v not in (None, False)})
                if not form.is_valid():
                    form_errors(form)
                cleaned = form.cleaned_data
            run(call, task, request.user, cleaned)
            if no_content:
                return Response(status=status.HTTP_204_NO_CONTENT)
            task.refresh_from_db()
            return _detail_response(request, task)

    _View.__name__ = "Task" + "".join(p.title() for p in name.replace("/", "-").split("-")) + "View"
    return _View


def _only_completed(task):
    from tasks.models import Task

    if task.state != Task.COMPLETED:
        raise serializers.ValidationError({"non_field_errors": ["Only completed tasks can be reopened."]})


def _division():
    from tasks import division
    return division


def _part_user(task, data):
    from rest_framework.exceptions import NotFound

    row = task.assignee_rows.select_related("user").filter(user_id=data["user"]).first()
    if row is None:
        raise NotFound("That person is not on this task.")
    return row.user


def _route_division(task, user, data):
    from accounts.selectors import staff_users
    from org.models import Division
    from rest_framework.exceptions import NotFound

    div = Division.objects.filter(pk=data["division"]).first()
    if div is None:
        raise NotFound("Division not found.")
    members = list(staff_users().filter(pk__in=data.get("members") or []))
    return _division().route_to_division(task, user, div, members=members, note=data.get("note", ""))


def _build_actions():
    from tasks import forms as f
    from tasks import services as ts

    return {
        "open": _action_view("open", None, None, lambda t, u, d: ts.open_task(t, u),
                             doc="Draft -> open; assignees are notified."),
        "accept": _action_view("accept", s.NoteSerializer, f.StartWorkForm, lambda t, u, d: ts.accept_task(t, u),
                               doc="Accept the task (my assignee row)."),
        "start": _action_view("start", s.NoteSerializer, f.StartWorkForm, lambda t, u, d: ts.start_work(t, u),
                              doc="Start work (refused while dependencies are open)."),
        "ask": _action_view("ask", s.RequiredNoteSerializer, f.AskQuestionForm,
                            lambda t, u, d: ts.ask_question(t, u, d.get("note", "")),
                            doc="Ask the creator a question (task -> awaiting response)."),
        "resume": _action_view("resume", s.NoteSerializer, f.ResumeForm,
                               lambda t, u, d: ts.resume_work(t, u, note=d.get("note", "")),
                               doc="Resume after a question / return."),
        "return": _action_view("return", s.RequiredReasonSerializer, f.ReturnForm,
                               lambda t, u, d: ts.return_to_assigner(t, u, d.get("reason", "")),
                               doc="Return the task to its assigner (reason required)."),
        "resend": _action_view("resend", s.NoteSerializer, f.ResendForm,
                               lambda t, u, d: ts.resend_task(t, u, note=d.get("note", "")),
                               doc="Creator re-sends a returned task."),
        "mark-done": _action_view("mark-done", s.NoteSerializer, f.MarkDoneForm,
                                  lambda t, u, d: ts.mark_assignee_done(t, u, note=d.get("note", "")),
                                  doc="Mark my part done (submits for review when everyone is done)."),
        "review": _action_view("review", s.TaskReviewSerializer, f.ReviewForm,
                               lambda t, u, d: ts.review_task(t, u, approve=d["approve"],
                                                              notes=d.get("notes") or ""),
                               doc="Approve (complete) or return for revision (notes required)."),
        "cancel": _action_view("cancel", s.ReasonSerializer, f.CancelForm,
                               lambda t, u, d: ts.cancel_task(t, u, reason=d.get("reason", "")),
                               doc="Cancel the task."),
        "reopen": _action_view("reopen", s.RequiredReasonSerializer, f.ReopenForm,
                               lambda t, u, d: ts.reopen_completed_task(t, u, reason=d["reason"]),
                               doc="Reopen a completed task (reason required).", pre=_only_completed),
        "delete": _action_view("delete", s.RequiredReasonSerializer, f.DeleteForm,
                               lambda t, u, d: ts.delete_task(t, u, reason=d["reason"]),
                               doc="Delete the task (reason required).", no_content=True),
        # ---- Phase C (H12, H14, H15, H23, H24): same services as the website ----
        "accept-start": _action_view("accept-start", None, None, lambda t, u, d: ts.accept_and_start(t, u),
                                     doc="Accept and start: my row goes in progress and my timer starts."),
        "confirm-part": _action_view("confirm-part", s.TaskPartSerializer, None,
                                     lambda t, u, d: ts.confirm_assignee_part(t, u, _part_user(t, d)),
                                     doc="Creator / reviewer confirms one person's finished part."),
        "return-part": _action_view("return-part", s.TaskPartSerializer, None,
                                    lambda t, u, d: ts.return_assignee_part(t, u, _part_user(t, d), d.get("reason", "")),
                                    doc="Creator / reviewer returns one person's part (reason required)."),
        "confirm-all": _action_view("confirm-all", s.NoteSerializer, None,
                                    lambda t, u, d: ts.confirm_all_parts(t, u, notes=d.get("note", "")),
                                    doc="Confirm everyone's work together (the task completes)."),
        "division/route": _action_view("division/route", s.TaskDivisionRouteSerializer, None, _route_division,
                                       doc="Route the task to a division of its department (capacity enforced)."),
        "division/accept": _action_view("division/accept", None, None,
                                        lambda t, u, d: _division().accept_division_task(t, u),
                                        doc="Division head accepts; the division's members are added."),
        "division/decline": _action_view("division/decline", s.RequiredReasonSerializer, None,
                                         lambda t, u, d: _division().decline_division_task(t, u, d.get("reason", "")),
                                         doc="Division head declines with a reason."),
        "division/complete": _action_view("division/complete", s.NoteSerializer, None,
                                          lambda t, u, d: _division().complete_division_task(t, u, d.get("note", "")),
                                          doc="Division head marks the group task complete."),
        "timer/start": _action_view("timer/start", None, None, lambda t, u, d: ts.start_timer(t, u),
                                    doc="Start my timer on this task."),
        "timer/stop": _action_view("timer/stop", None, None, lambda t, u, d: ts.stop_timer(t, u),
                                   doc="Stop my running timer (logs the time)."),
        "log-time": _action_view("log-time", s.TaskLogTimeSerializer, f.LogTimeForm,
                                 lambda t, u, d: ts.log_time(t, u, started_at=d["started_at"],
                                                             ended_at=d["ended_at"], note=d.get("note") or ""),
                                 doc="Log a time range manually (assignees; max 24 h per entry)."),
    }


ACTIONS = _build_actions()


class TaskAssigneesView(APIView):
    @extend_schema(operation_id="tasks_assignees_add", request=s.TaskAssigneesAddSerializer,
                   responses={200: s.TaskAssigneesResultSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        """Add assignees (tasks.services.add_assignee per user; a draft with
        assignees opens). Users the rules refuse are listed in ``skipped``."""
        task = _task(request, pk)
        body = s.TaskAssigneesAddSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        added, skipped = svc.add_assignees(task, request.user, body.validated_data["user_ids"])
        task.refresh_from_db()
        return Response(s.TaskAssigneesResultSerializer({
            "added": added, "skipped": skipped, "task": svc.detail(task, request.user, request)}).data)


class TaskAssigneeRemoveView(APIView):
    @extend_schema(operation_id="tasks_assignees_remove", request=None,
                   responses={200: s.TaskDetailSerializer, **OBJ_ERRORS})
    def delete(self, request, pk, user_id):
        from accounts.models import User
        from tasks import services as ts

        task = _task(request, pk)
        assignee = get_object_or_404(User, pk=user_id)
        run(ts.remove_assignee, task, request.user, assignee)
        return _detail_response(request, task)


class TaskCommentsView(APIView):
    @extend_schema(operation_id="tasks_comment", request=s.TaskCommentRequestSerializer,
                   responses={201: s.TaskDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        from tasks import services as ts
        from tasks.forms import CommentForm

        task = _task(request, pk)
        body = s.TaskCommentRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        form = CommentForm({"body": body.validated_data["body"],
                            "is_internal": "on" if body.validated_data["is_internal"] else "",
                            "pinned": "on" if body.validated_data["pinned"] else ""})
        if not form.is_valid():
            form_errors(form)
        run(ts.add_comment, task, request.user, body=form.cleaned_data["body"],
            internal=form.cleaned_data.get("is_internal", False), pinned=form.cleaned_data.get("pinned", False))
        return _detail_response(request, task, 201)


class TaskCommentDeleteView(APIView):
    @extend_schema(operation_id="tasks_comment_delete", request=None,
                   responses={204: None, **OBJ_ERRORS})
    def delete(self, request, pk, comment_id):
        from tasks import services as ts
        from tasks.models import TaskComment

        task = _task(request, pk)
        comment = get_object_or_404(TaskComment, pk=comment_id, task=task)
        run(ts.delete_comment, task, request.user, comment)
        return Response(status=204)


class TaskAttachmentsView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(operation_id="tasks_attach", request={"multipart/form-data": s.AttachmentUploadSerializer},
                   responses={201: s.TaskDetailSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        from tasks import services as ts
        from tasks.forms import AttachmentForm

        task = _task(request, pk)
        form = AttachmentForm(request.POST, request.FILES)
        if not form.is_valid():
            form_errors(form)
        run(ts.add_attachment, task, request.user, file=form.cleaned_data["file"],
            description=form.cleaned_data.get("description") or "")
        return _detail_response(request, task, 201)


class TaskAttachmentView(APIView):
    @extend_schema(operation_id="tasks_attachment_download",
                   responses={(200, "*/*"): OpenApiResponse(OpenApiTypes.BINARY, description="The file."),
                              **OBJ_ERRORS})
    def get(self, request, pk, attachment_id):
        from tasks.models import TaskAttachment

        task = _task(request, pk)
        att = get_object_or_404(TaskAttachment, pk=attachment_id, task=task)
        return download(request, att)

    @extend_schema(operation_id="tasks_attachment_delete", request=None, responses={204: None, **OBJ_ERRORS})
    def delete(self, request, pk, attachment_id):
        from tasks import services as ts
        from tasks.models import TaskAttachment

        task = _task(request, pk)
        att = get_object_or_404(TaskAttachment, pk=attachment_id, task=task)
        run(ts.delete_attachment, task, request.user, att)
        return Response(status=204)


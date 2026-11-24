"""V3-only execution status. Reporting never changes model inputs or outputs."""
import logging


class ExecutionProgress:
    def __init__(self, project_id, segment_index):
        self.project_id = str(project_id)
        self.segment_index = int(segment_index)
        try:
            from comfy_execution.utils import get_executing_context
            self.context = get_executing_context()
        except (ImportError, AttributeError):
            self.context = None

    def __call__(self, phase, detail=""):
        logging.info("[DigitalHumanV3] 分镜 %s · %s%s", self.segment_index + 1,
                     phase, f" · {detail}" if detail else "")
        if self.context is None:
            return
        try:
            from server import PromptServer
            PromptServer.instance.send_sync("xiantu_dhv3_progress", {
                "prompt_id": self.context.prompt_id,
                "node": self.context.node_id,
                "project_id": self.project_id,
                "segment_index": self.segment_index,
                "phase": phase, "detail": detail,
            })
        except Exception:
            logging.debug("DigitalHumanV3 status delivery failed", exc_info=True)


class ProgressProxy:
    def __init__(self, target, progress, methods):
        self._target, self._progress, self._methods = target, progress, methods

    def __getattr__(self, name):
        original = getattr(self._target, name)
        phase = self._methods.get(name)
        if phase is None:
            return original

        def measured(*args, **kwargs):
            self._progress(phase)
            return original(*args, **kwargs)
        return measured

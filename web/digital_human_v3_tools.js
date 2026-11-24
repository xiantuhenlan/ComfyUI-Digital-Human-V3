import { openFunctionMarks, createFunctionSelections } from "./digital_human_v3_marks.js";

// 3D stays in ComfyUI-3D-Director. Only this caller owns V3's scene writeback.
export function installDirectorTools({root, owner, getScene, getState, save, applyScene, notify, isBusy}) {
    const selections=createFunctionSelections(owner,{getState,getScene,save,isBusy});
    let modal = null, marks = null, disposed = false, opening = false;
    const cameraButton = root.querySelector("[data-open-3d-director]");
    const marksButton = root.querySelector("[data-open-function-marks]");
    const escape3D = event => {
        if (event.key !== "Escape" || !modal?.isConnected) return;
        event.preventDefault();event.stopImmediatePropagation();
        modal.querySelector("[data-cancel]")?.click();
    };
    if (cameraButton) cameraButton.onclick = async () => {
        if (opening || disposed) return;
        const provider = globalThis.ComfyUI3DDirector;
        if (typeof provider?.open !== "function") {
            notify("未加载 ComfyUI-3D-Director，请启用闲兔3D导演台插件后刷新页面", true);
            return;
        }
        const scene = getScene();
        if (!scene) return;
        const originId = String(scene.id), contextId = `${owner.id}:${originId}`;
        const previous = new Set(document.querySelectorAll(".x3dd-overlay"));
        opening = true;cameraButton.disabled = true;
        try {
            const pending = provider.open({contextId, prompt:String(scene.original_prompt || ""), settings:structuredClone(scene.director_3d || {})});
            modal = [...document.querySelectorAll(".x3dd-overlay")].find(element => !previous.has(element)) || null;
            if (modal) {
                // Provider's normal z-index is below the workbench overlay.
                modal.style.zIndex = "1000060";
                modal.dataset.dhv3Owner = String(owner.id);
                document.addEventListener("keydown", escape3D, true);
            }
            const result = await pending;
            if (!disposed && result && result.contextId === contextId) await applyScene(originId, result);
        } catch (error) {
            if (!disposed) notify(error.message || "3D 导演台打开失败", true);
        } finally {
            document.removeEventListener("keydown", escape3D, true);
            modal = null;opening = false;cameraButton.disabled = false;
            if (!disposed && root.isConnected && !root.hidden) cameraButton.focus();
        }
    };
    marksButton.onclick = () => {
        if (disposed) return;
        if (marks?.isConnected) return marks.querySelector("[data-placeholder-close]")?.focus();
        marks = openFunctionMarks(owner, {isBusy,selections});
    };
    return {
        syncMarks(){selections.apply();marks?._refresh?.();},
        applyMarks(){selections.apply({force:true});},
        closeDialogs() { modal?.querySelector("[data-cancel]")?.click();marks?._close?.();marks=null; },
        dispose() { disposed=true;this.closeDialogs();document.removeEventListener("keydown",escape3D,true);if (cameraButton) cameraButton.onclick=null;marksButton.onclick=null; },
    };
}

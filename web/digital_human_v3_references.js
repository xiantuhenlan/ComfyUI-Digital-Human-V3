// Global applies to each image; scene-local references remain independent.
const referenceKey = ref => ref?.shared_asset_id ? `shared:${ref.shared_asset_id}` : `path:${ref?.path || ""}`;

function prepareReferences(state) {
    state.settings ||= {};
    if (!Array.isArray(state.global_refs)) state.global_refs = [];
    if (state.reference_scope_version !== 2) {
        const enabled=Boolean(state.settings.reference_global);
        state.global_refs=state.global_refs.map(ref=>({...ref,is_global:enabled}));
        state.reference_scope_version=2;
        state.settings.reference_global=false;
    }
}

function mergedReferences(local, shared) {
    const seen=new Set();
    return [...local,...shared.filter(ref=>ref.is_global!==false)].filter(ref=>{
        const key=referenceKey(ref);if(seen.has(key))return false;seen.add(key);return true;
    });
}

export function referenceImages(state, segmentIndex = state?.selected_segment ?? 0, globalScope) {
    if (!state) return [];
    prepareReferences(state);
    if (globalScope===true) return state.global_refs;
    const segment = state.segments?.[segmentIndex];
    if (!segment) return [];
    if (!Array.isArray(segment.refs)) segment.refs = [];
    return globalScope===false ? segment.refs : mergedReferences(segment.refs,state.global_refs);
}

export function isReferenceGlobal(state, ref) {
    prepareReferences(state);
    return state.global_refs.some(item=>item.is_global!==false&&referenceKey(item)===referenceKey(ref));
}

export function setReferenceGlobal(state, index, enabled, segmentIndex=state.selected_segment) {
    const ref=referenceImages(state,segmentIndex)[index];
    if (!ref) return;
    const key=referenceKey(ref), shared=state.global_refs.find(item=>referenceKey(item)===key);
    if (enabled) {
        const proposed=state.global_refs.filter(item=>referenceKey(item)!==key).concat({...ref,is_global:true});
        const overflow=state.segments.findIndex(segment=>mergedReferences(segment.refs||[],proposed).length>9);
        if (overflow>=0) throw Error(`分镜 ${overflow+1} 的参考图合计超过9张，请先移除不需要的图片`);
        if (shared) shared.is_global=true;else state.global_refs.push({...ref,is_global:true});
    } else {
        if (shared) shared.is_global=false;
        const local=referenceImages(state,segmentIndex,false);
        if (!local.some(item=>referenceKey(item)===key)) local.push({...ref,is_global:false});
    }
}

export function removeReferenceImage(state, index, segmentIndex = state.selected_segment) {
    const records = referenceImages(state, segmentIndex);
    if (!Number.isInteger(index) || index < 0 || index >= records.length) return null;
    const ref=records[index],key=referenceKey(ref);
    if (isReferenceGlobal(state,ref)) {
        state.global_refs=state.global_refs.filter(item=>referenceKey(item)!==key);
        for (const segment of state.segments) segment.refs=(segment.refs||[]).filter(item=>referenceKey(item)!==key);
    } else {
        state.segments[segmentIndex].refs=referenceImages(state,segmentIndex,false).filter(item=>referenceKey(item)!==key);
    }
    return ref;
}

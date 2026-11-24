import { app } from "../../scripts/app.js";

const NODE = "XiantuDigitalHumanStudioV3";
const NODE_TAGS = "xiantuDigitalHumanV3Tags", GROUP_TAGS = "xiantuDigitalHumanV3Groups";
const LABELS = {speed:"速度", optimization:"性能优化", sampling:"采样 SIGMAS", lora:"LoRA", model:"模型", custom:"自定义", groups:"工作流组"};
const esc = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const graphOf = owner => owner.graph || app.graph;
const typeOf = node => String(node.comfyClass || node.type || "");
const owners = graph => (graph?._nodes || []).filter(node=>typeOf(node)===NODE);
const isDirector = node => /XiantuDigitalHumanStudio|XiantuVideoEditorWorkbench|ComfyUIH3ImageWorkbench|FullDirector|MotionTransferDirector/.test(typeOf(node));
const changed = graph => {graph?.change?.();graph?.setDirtyCanvas?.(true,true);window.dispatchEvent(new CustomEvent("xiantu-dhv3-function-tags-changed",{detail:graph}));};
const groupsOf = graph => graph?._groups || [];
const groupKey = group => group.id != null ? `id:${group.id}` : `group:${group.title}:${JSON.stringify(Array.from(group.pos || group._pos || [0,0]))}`;
function groupStore(owner, create=false) {
    const graph=graphOf(owner);
    if (create) {graph.extra??={};graph.extra[GROUP_TAGS]??={};graph.extra[GROUP_TAGS][String(owner.id)]??={};}
    return graph?.extra?.[GROUP_TAGS]?.[String(owner.id)] || {};
}
function resolveGroup(graph, key, record) {
    const groups=groupsOf(graph), exact=groups.find(group=>groupKey(group)===key);
    if(exact)return exact;
    const sameTitle=groups.filter(group=>String(group.title)===record.title);
    return sameTitle.length===1?sameTitle[0]:null;
}
export function readFunctionTag(node, owner) {return node.properties?.[NODE_TAGS]?.[String(owner.id)] || null;}
export function writeFunctionTag(node, owner, tag) {
    node.properties??={};node.properties[NODE_TAGS]??={};
    if(tag)node.properties[NODE_TAGS][String(owner.id)]={category:String(tag.category||"custom"),label:String(tag.label||""),note:String(tag.note||"")};
    else delete node.properties[NODE_TAGS][String(owner.id)];
    changed(graphOf(owner));
}
export function markFunctionGroup(owner, group, marked=true) {
    const store=groupStore(owner,true), key=groupKey(group);
    if(marked)store[key]??={title:String(group.title||"未命名组"),note:""};else delete store[key];
    changed(graphOf(owner));
}
function membersOf(group, owner) {
    group.recomputeInsideNodes?.();
    return Array.from(group.nodes || group._nodes || []).filter(node=>!isDirector(node) && node!==owner);
}
function changeMode(nodes, owner, mode) {
    const graph=graphOf(owner);graph.beforeChange?.();
    for(const node of nodes)node.mode=mode;
    graph.afterChange?.();changed(graph);
}
function chooseTag(node,owner,category) {
    const current=readFunctionTag(node,owner), label=category==="custom"?window.prompt("标注名称",current?.label||""):"";
    if(category==="custom"&&label===null)return;
    writeFunctionTag(node,owner,{category,label,note:current?.note||""});
}
function nodeMenu(node,owner) {
    const current=readFunctionTag(node,owner);
    const entries=Object.entries(LABELS).filter(([key])=>key!=="groups").map(([category,label])=>({content:`${current?.category===category?"✓ ":""}${label}`,callback:()=>chooseTag(node,owner,category)}));
    if(current)entries.push(null,{content:"取消标注",callback:()=>writeFunctionTag(node,owner,null)});
    return entries;
}
function menuForOwners(graph, build) {
    const list=owners(graph);
    if(list.length===1)return build(list[0]);
    return list.map(owner=>({content:`${owner.title||"数字人 V3"} #${owner.id}`,has_submenu:true,submenu:{options:build(owner)}}));
}
app.registerExtension({
    name:"Xiantu.DigitalHumanV3.FunctionMarks",
    beforeRegisterNodeDef(nodeType) {
        const prototype=nodeType.prototype;
        if(Object.hasOwn(prototype,"__dhv3MarksInstalled"))return;
        Object.defineProperty(prototype,"__dhv3MarksInstalled",{value:true});
        const previousMenu=prototype.getExtraMenuOptions;
        prototype.getExtraMenuOptions=function(canvas,options){
            const result=previousMenu?.apply(this,arguments), graph=this.graph||app.graph;
            if(Array.isArray(options)&&!isDirector(this)&&owners(graph).length)options.unshift({content:"数字人 V3 功能标注",has_submenu:true,submenu:{options:menuForOwners(graph,owner=>nodeMenu(this,owner))}});
            return result;
        };
        const previousDraw=prototype.onDrawForeground;
        prototype.onDrawForeground=function(context){
            previousDraw?.apply(this,arguments);
            if(this.flags?.collapsed)return;
            const owner=owners(this.graph||app.graph).find(item=>readFunctionTag(this,item));
            if(!owner)return;
            const tag=readFunctionTag(this,owner), label=`V3 · ${tag.label||LABELS[tag.category]||"标注"}`;
            context.save();context.font='12px "Microsoft YaHei",sans-serif';
            const width=Math.min(Number(this.size?.[0]||180),context.measureText(label).width+16);
            context.fillStyle="#10333b";context.strokeStyle="#2bbabf";context.lineWidth=1;context.fillRect(0,-46,width,18);context.strokeRect(0,-46,width,18);context.fillStyle="#a2eded";context.fillText(label,7,-33,width-12);context.restore();
        };
    },
    getCanvasMenuItems(canvas){
        const graph=canvas?.graph||app.graph;
        if(!owners(graph).length)return [];
        const point=canvas?.graph_mouse, group=point&&graph.getGroupOnPos?.(point[0],point[1]);
        if(!group)return [];
        return [{content:"数字人 V3：标注此组",has_submenu:true,submenu:{options:menuForOwners(graph,owner=>[{content:groupStore(owner)[groupKey(group)]?"取消组标注":"标注此组",callback:()=>markFunctionGroup(owner,group,!groupStore(owner)[groupKey(group)])}])}}];
    },
});

const PANEL_LABELS={speed:"速度",optimization:"性能优化",sampling:"采样 SIGMAS",lora:"LoRA",groups:"工作流组"};
const nodeId=node=>String(node?.id??"");
const modeOf=value=>[0,2,4].includes(Number(value))?Number(value):0;
function graphScope(owner){
    const graph=graphOf(owner),nodes=graph?._nodes||[],parents=new Map(),children=new Map(),setters=new Map();
    const link=id=>graph?.links instanceof Map?graph.links.get(id):graph?.links?.[id];
    for(const node of nodes)if(node.type==='SetNode'&&typeof node.widgets?.[0]?.value==='string'){
        const name=node.widgets[0].value;setters.set(name,setters.has(name)?null:node);
    }
    for(const node of nodes){
        const id=nodeId(node),origins=(node.inputs||[]).flatMap(input=>{const l=link(input.link);return l?[String(Array.isArray(l)?l[1]:l.origin_id)]:[];});
        const setter=node.type==='GetNode'?setters.get(node.widgets?.[0]?.value):null;
        if(setter?.inputs?.[0]?.link!=null&&link(setter.inputs[0].link))origins.push(nodeId(setter));
        parents.set(id,origins);for(const origin of origins){if(!children.has(origin))children.set(origin,[]);children.get(origin).push(id);}
    }
    const visit=(starts,edges)=>{const seen=new Set(),pending=[...starts];while(pending.length){const id=String(pending.pop());if(seen.has(id))continue;const item=nodes.find(n=>nodeId(n)===id);if(id!==nodeId(owner)&&item&&isDirector(item))continue;seen.add(id);pending.push(...(edges.get(id)||[]));}return seen;};
    return visit(visit([nodeId(owner)],children),parents);
}

// Same selection rules as the original director, with V3-owned project fields.
export function createFunctionSelections(owner,{getState=()=>null,getScene=()=>null,save=async()=>{},isBusy=()=>false}={}){
    const graph=graphOf(owner),local={segments:[]};
    const project=()=>getState()||local;
    const ownNodes=category=>(graph?._nodes||[]).filter(node=>node!==owner&&!isDirector(node)&&readFunctionTag(node,owner)?.category===category);
    const nodes=category=>{const scope=graphScope(owner);return ownNodes(category).filter(node=>scope.has(nodeId(node)));};
    const modes=category=>Object.fromEntries(nodes(category).map(node=>[nodeId(node),modeOf(node.mode)]));
    const config=()=>{const state=project();state.function_mark_selections_v3??={};const c=state.function_mark_selections_v3;c.defaults??={};c.scopes??={};c.scopes.speedOptimizationGlobal??=true;c.scopes.samplingGlobal??=false;c.scopes.loraGlobalByNode??={};c.pairs??={};return c;};
    const sceneConfig=()=>{const scene=getScene();if(!scene)return {};scene.function_mark_selection_v3??={};return scene.function_mark_selection_v3;};
    const defaults=()=>{const c=config(),d=c.defaults;const sigma=nodes('sampling');if(!sigma.some(n=>nodeId(n)===String(d.samplingNodeId||'')))d.samplingNodeId=nodeId(sigma.find(n=>Number(n.mode||0)===0)||sigma[0]);for(const category of ['speed','optimization','lora']){const key=category+'NodeModes';d[key]??={};for(const node of nodes(category))d[key][nodeId(node)]??=modeOf(node.mode);}return d;};
    const commit=(node,mode)=>{if(Number(node.mode||0)===mode)return;node.mode=mode;graph?.change?.();graph?.setDirtyCanvas?.(true,true);};
    const samplerName=node=>String(node.widgets?.find(w=>String(w.name).toLowerCase()==='sampler_name')?.value||'').trim().toLowerCase();
    const candidates=()=>{const scope=graphScope(owner),link=id=>graph.links instanceof Map?graph.links.get(id):graph.links?.[id];return (graph._nodes||[]).filter(node=>scope.has(nodeId(node))&&samplerName(node)&&(node.outputs||[]).some(output=>String(output.type).toUpperCase()==='SAMPLER'&&(output.links||[]).some(id=>{const l=link(id),target=graph.getNodeById?.(Array.isArray(l)?l[3]:l?.target_id);return typeOf(target||{})==='XiantuSamplerConditionalRelay';})));};
    const pairMappings=()=>{const c=config(),tagged=nodes('sampling'),all=candidates();for(const [id,pair] of Object.entries(c.pairs))if(!tagged.some(n=>nodeId(n)===id)||!all.some(n=>nodeId(n)===String(pair)))delete c.pairs[id];const used=new Set(Object.values(c.pairs));tagged.forEach((node,index)=>{const id=nodeId(node);if(c.pairs[id])return;let paired=index<3?all.find(n=>samplerName(n)==='er_sde'):index===3?all.find(n=>samplerName(n)==='res_multistep'):null;paired??=all.find(n=>!used.has(nodeId(n)))||all[0];if(paired){c.pairs[id]=nodeId(paired);used.add(nodeId(paired));}});return all;};
    const syncPair=(force=false)=>{const all=pairMappings(),tagged=nodes('sampling'),selected=tagged.find(n=>Number(n.mode||0)===0)||tagged[0],paired=all.find(n=>nodeId(n)===config().pairs[nodeId(selected)]);if(selected&&paired&&(!isBusy()||force))for(const node of all)commit(node,node===paired?0:4);return {all,paired,selected};};
    const applyGroups=()=>{const changes=new Map();for(const [key,record] of Object.entries(groupStore(owner))){if(record.mode!==0&&record.mode!==4)continue;const group=resolveGroup(graph,key,record);if(group)for(const node of membersOf(group,owner))if(changes.get(node)!==4)changes.set(node,record.mode);}for(const [node,mode] of changes)commit(node,mode);};
    const apply=({force=false}={})=>{
        if(isBusy()&&!force)return;
        if(!project().function_mark_selections_v3&&!Object.keys(groupStore(owner)).length&&!['speed','optimization','sampling','lora'].some(c=>ownNodes(c).length))return;
        const c=config(),d=defaults(),s=sceneConfig(),sigmas=nodes('sampling');
        const wanted=!c.scopes.samplingGlobal&&s.samplingNodeId?s.samplingNodeId:d.samplingNodeId;
        const selected=sigmas.find(n=>nodeId(n)===String(wanted))||sigmas.find(n=>nodeId(n)===d.samplingNodeId)||sigmas[0];
        for(const node of sigmas)if(node===selected)commit(node,0);else if(Number(node.mode||0)===0)commit(node,4);
        syncPair(force);
        for(const category of ['speed','optimization','lora'])for(const node of nodes(category)){
            const id=nodeId(node),key=category+'NodeModes',global=category==='lora'?c.scopes.loraGlobalByNode[id]:c.scopes.speedOptimizationGlobal;
            const value=global?d[key][id]:(s[key]?.[id]??d[key][id]);commit(node,modeOf(value));
        }
        applyGroups();
    };
    const persist=()=>{void save();};
    const api={
        config,defaults,nodes,ownNodes,modes,candidates,samplerName,pairMappings,syncPair,apply,
        setNodeMode(node,category,mode){
            if(isBusy())return;const c=config(),d=defaults(),s=sceneConfig();
            if(category==='sampling'){
                const tagged=nodes(category);if(mode===0){for(const n of tagged)commit(n,n===node?0:4);}else {const enabled=Number(node.mode||0)===0;commit(node,mode);if(enabled){const replacement=tagged.find(n=>n!==node&&Number(n.mode||0)===0)||tagged.find(n=>n!==node)||node;commit(replacement,0);}}
                const selected=tagged.find(n=>Number(n.mode||0)===0);if(selected){if(c.scopes.samplingGlobal){d.samplingNodeId=nodeId(selected);for(const target of project().segments||[])if(target.function_mark_selection_v3)delete target.function_mark_selection_v3.samplingNodeId;}else s.samplingNodeId=nodeId(selected);}syncPair();
            }else{
                commit(node,mode);const id=nodeId(node),key=category+'NodeModes',global=category==='lora'?c.scopes.loraGlobalByNode[id]:c.scopes.speedOptimizationGlobal;
                if(global){if(category==='lora'){d[key][id]=mode;for(const target of project().segments||[])if(target.function_mark_selection_v3?.[key])delete target.function_mark_selection_v3[key][id];}else {d[key]=modes(category);for(const target of project().segments||[])if(target.function_mark_selection_v3)delete target.function_mark_selection_v3[key];}}
                else {s[key]??={};if(category==='lora')s[key][id]=mode;else s[key]=modes(category);}
            }
            persist();
        },
        setGlobal(category,enabled,node=null){
            if(isBusy())return;const c=config(),d=defaults();if(category==='lora'){const id=nodeId(node);c.scopes.loraGlobalByNode[id]=enabled;if(enabled){d.loraNodeModes[id]=modeOf(node.mode);for(const target of project().segments||[])if(target.function_mark_selection_v3?.loraNodeModes)delete target.function_mark_selection_v3.loraNodeModes[id];}}
            else if(category==='sampling'){c.scopes.samplingGlobal=enabled;if(enabled){d.samplingNodeId=nodeId(nodes('sampling').find(n=>Number(n.mode||0)===0));for(const target of project().segments||[])if(target.function_mark_selection_v3)delete target.function_mark_selection_v3.samplingNodeId;}}
            else {c.scopes.speedOptimizationGlobal=enabled;if(enabled)for(const cat of ['speed','optimization']){const key=cat+'NodeModes';d[key]=modes(cat);for(const target of project().segments||[])if(target.function_mark_selection_v3)delete target.function_mark_selection_v3[key];}}
            persist();
        },
        useDefault(category){if(isBusy())return;const s=sceneConfig();delete s[category==='sampling'?'samplingNodeId':'loraNodeModes'];apply();persist();},
        hasOverride(category){const s=sceneConfig();return category==='sampling'?Boolean(s.samplingNodeId):Object.keys(s.loraNodeModes||{}).some(id=>!config().scopes.loraGlobalByNode[id]);},
        setPair(node,id){if(isBusy())return;config().pairs[nodeId(node)]=String(id);syncPair();persist();},
        setGroupMode(key,record,mode){if(isBusy())return;record.mode=mode;groupStore(owner,true)[key]=record;graph.beforeChange?.();applyGroups();graph.afterChange?.();changed(graph);},
    };
    return api;
}

export function openFunctionMarks(owner,{isBusy=()=>false,selections=createFunctionSelections(owner)}={}){
    const graph=graphOf(owner),root=document.createElement('div');root.className='dh3m-overlay';
    root.innerHTML=`<section class="dh3m-dialog" role="dialog" aria-modal="true" aria-label="标记功能"><header><b>标记功能</b><div class="dh3m-header-actions"><label><input type="checkbox" data-speed-optimization-global> 全局</label><label hidden><input type="checkbox" data-sampling-global> 全局</label><button type="button" data-close-marks data-placeholder-close aria-label="关闭">×</button></div></header><main class="dh3m-main"><nav class="dh3m-filters">${Object.entries(PANEL_LABELS).map(([key,label])=>`<button type="button" data-function-tag-filter="${key}">${label}</button>`).join('')}</nav><div class="dh3m-list" data-function-tag-list></div><div data-mark-tab="sampling" hidden><section class="dh3m-sigma-tagged"><div class="dh3m-config-head"><strong>采样标记节点</strong><span data-sampling-shot-state></span><button data-use-default="sampling">使用默认</button></div><div class="dh3m-list compact" data-sampling-tag-list></div></section><section class="dh3m-sampler-pairing"><strong>采样器选择（自动配对）</strong><div class="dh3m-pair-list" data-sampler-pair-list></div></section></div><div data-mark-tab="lora" hidden><div class="dh3m-config-head"><strong>LoRA 标记节点</strong><span data-lora-shot-state></span><button data-use-default="lora">使用默认</button></div><div class="dh3m-list" data-lora-tag-list></div></div></main><footer><button type="button" data-placeholder-close>关闭</button></footer></section>`;
    const q=s=>root.querySelector(s),qa=s=>[...root.querySelectorAll(s)];let category='speed',closed=false;
    const refresh=event=>{if(event.detail===graph&&!closed)render();};
    const close=()=>{closed=true;window.removeEventListener('xiantu-dhv3-function-tags-changed',refresh);document.removeEventListener('keydown',onKey,true);root.remove();};
    const onKey=event=>{if(event.key==='Escape'&&!event.target.matches('[data-note-input]')){event.preventDefault();event.stopImmediatePropagation();close();}};
    root._close=close;root._refresh=()=>{if(!closed)render();};qa('[data-placeholder-close]').forEach(b=>b.onclick=close);root.onclick=e=>{if(e.target===root)close();};
    qa('[data-function-tag-filter]').forEach(b=>b.onclick=()=>{category=b.dataset.functionTagFilter;render();});
    q('[data-speed-optimization-global]').onchange=e=>{selections.setGlobal(category,e.target.checked);render();};q('[data-sampling-global]').onchange=e=>{selections.setGlobal('sampling',e.target.checked);render();};
    qa('[data-use-default]').forEach(b=>b.onclick=()=>{selections.useDefault(b.dataset.useDefault);render();});
    function nodeRows(list,cat){
        list.replaceChildren();const tagged=selections.nodes(cat),c=selections.config();if(cat==='sampling')selections.pairMappings();
        for(const node of tagged){
            const tag=readFunctionTag(node,owner),row=document.createElement('article');row.className='dh3m-row'+(cat==='lora'?' has-row-global':cat==='sampling'?' has-sampling-pair':'');
            let note=String(tag?.note||'').trim().slice(0,160),editing=false;
            const extra=cat==='lora'?`<label class="dh3m-row-global"><input type="checkbox" data-lora-global ${c.scopes.loraGlobalByNode[nodeId(node)]?'checked':''}> 全局</label>`:cat==='sampling'?`<label class="dh3m-sampling-pair">搭配<select data-sampling-pair>${selections.candidates().map(n=>`<option value="${esc(nodeId(n))}" ${c.pairs[nodeId(node)]===nodeId(n)?'selected':''}>${esc(selections.samplerName(n))}</option>`).join('')}</select></label>`:'';
            row.innerHTML=`<div><b>${esc(node.title||typeOf(node)||'未命名节点')}</b><span>${esc(tag?.label||LABELS[tag?.category]||'已标记')} · 节点 ${esc(nodeId(node))}</span></div><div class="dh3m-note-box"><span class="dh3m-note-text${note?'':' empty'}" data-note-text>${esc(note||'暂无备注')}</span><input class="dh3m-note" type="text" maxlength="160" data-note-input value="${esc(note)}" placeholder="备注这个节点的功能" hidden></div>${extra}<button type="button" class="dh3m-note-edit" data-note-edit>编辑</button><div class="dh3m-modes"><button type="button" data-node-mode="0">启用</button><button type="button" data-node-mode="4">忽略</button><button type="button" data-node-mode="2">关闭</button></div>`;
            const input=row.querySelector('[data-note-input]'),text=row.querySelector('[data-note-text]'),edit=row.querySelector('[data-note-edit]');
            const finish=commit=>{if(!editing)return;editing=false;const value=commit?input.value.trim().slice(0,160):note;input.hidden=true;text.hidden=false;text.textContent=value||'暂无备注';text.classList.toggle('empty',!value);edit.textContent='编辑';if(commit&&value!==note){note=value;writeFunctionTag(node,owner,{...readFunctionTag(node,owner),note:value});}};
            edit.onclick=()=>{if(editing){finish(true);return;}editing=true;input.hidden=false;text.hidden=true;edit.textContent='保存';input.focus();input.select();};
            input.onkeydown=e=>{if(e.key==='Enter'||e.key==='Escape'){e.preventDefault();e.stopPropagation();finish(e.key==='Enter');}};
            row.querySelectorAll('[data-node-mode]').forEach(b=>{const mode=Number(b.dataset.nodeMode);b.classList.toggle('active',Number(node.mode||0)===mode);b.disabled=isBusy();b.onclick=()=>{selections.setNodeMode(node,cat,mode);render();};});
            const global=row.querySelector('[data-lora-global]');if(global){global.disabled=isBusy();global.onchange=()=>{selections.setGlobal('lora',global.checked,node);render();};}
            const pair=row.querySelector('[data-sampling-pair]');if(pair){pair.disabled=isBusy();pair.onchange=()=>{selections.setPair(node,pair.value);render();};}
            list.append(row);
        }
        for(const node of selections.ownNodes(cat).filter(node=>!tagged.includes(node))){const row=document.createElement('article');row.className='dh3m-row';row.innerHTML=`<div><b>${esc(node.title||typeOf(node))}</b><span>未接入当前执行链 · #${esc(nodeId(node))}</span></div>`;list.append(row);}
        if(!list.children.length)list.innerHTML=`<div class="dh3m-empty">暂无“${esc(LABELS[cat])}”标记节点，请在节点右键菜单中添加功能标记。</div>`;
    }
    function render(){
        const c=selections.config();selections.defaults();qa('[data-function-tag-filter]').forEach(b=>b.classList.toggle('active',b.dataset.functionTagFilter===category));const specialized=category==='sampling'||category==='lora';q('[data-function-tag-list]').hidden=specialized;qa('[data-mark-tab]').forEach(p=>p.hidden=p.dataset.markTab!==category);
        q('[data-speed-optimization-global]').parentElement.hidden=!['speed','optimization'].includes(category);q('[data-speed-optimization-global]').checked=Boolean(c.scopes.speedOptimizationGlobal);q('[data-speed-optimization-global]').disabled=isBusy();q('[data-sampling-global]').parentElement.hidden=category!=='sampling';q('[data-sampling-global]').checked=Boolean(c.scopes.samplingGlobal);q('[data-sampling-global]').disabled=isBusy();
        if(category==='groups'){
            const list=q('[data-function-tag-list]');list.replaceChildren();
            for(const [key,record] of Object.entries(groupStore(owner))){const group=resolveGroup(graph,key,record);if(!group)continue;const nodes=membersOf(group,owner),modes=new Set(nodes.map(n=>Number(n.mode||0))),state=!nodes.length?'空组':modes.size===1?({0:'已启用',2:'已关闭',4:'已忽略'}[modes.values().next().value]||'自定义状态'):'混合状态',row=document.createElement('article');row.className='dh3m-row dh3m-group-row';row.innerHTML=`<div><b title="${esc(group.title)}">${esc(group.title||'未命名组')}</b><span>组 · ${nodes.length} 个节点 · ${state}</span></div><div></div><div class="dh3m-modes"><button type="button" data-group-mode="0">启用</button><button type="button" data-group-mode="4">忽略</button></div>`;row.querySelectorAll('[data-group-mode]').forEach(b=>{const mode=Number(b.dataset.groupMode);b.classList.toggle('active',modes.size===1&&modes.has(mode));b.disabled=!nodes.length||isBusy();b.onclick=()=>{selections.setGroupMode(key,record,mode);render();};});list.append(row);}
            if(!list.children.length)list.innerHTML='<div class="dh3m-empty">暂无标记组。右键画布上的组标题，选择「数字人 V3：标注此组」。</div>';
        }else if(specialized){
            nodeRows(q(category==='sampling'?'[data-sampling-tag-list]':'[data-lora-tag-list]'),category);
            if(category==='sampling'){const {all,paired}=selections.syncPair(),list=q('[data-sampler-pair-list]');list.innerHTML=all.map(node=>`<article class="dh3m-pair-row${node===paired?' active':''}"><div><b>${esc(selections.samplerName(node))}</b><span>${selections.samplerName(node)==='er_sde'?'上方 3 个 SIGMAS 专用':selections.samplerName(node)==='res_multistep'?'原最下方 SIGMAS 专用':'与新增 SIGMAS 动态配对'} · 节点 ${esc(nodeId(node))}</span></div><button disabled>${node===paired?'已自动匹配':'未匹配'}</button></article>`).join('')||'<div class="dh3m-empty">暂无接入采样器中转的采样器选择节点</div>';}
        }else nodeRows(q('[data-function-tag-list]'),category);
        const sigmaOverride=selections.hasOverride('sampling'),loraOverride=selections.hasOverride('lora');q('[data-sampling-shot-state]').textContent=c.scopes.samplingGlobal?'全局设置':sigmaOverride?'当前分镜：独立设置':'当前分镜：默认设置';q('[data-lora-shot-state]').textContent=loraOverride?'当前分镜含独立设置':'当前分镜：默认设置';q('[data-use-default=sampling]').disabled=isBusy()||c.scopes.samplingGlobal||!sigmaOverride;q('[data-use-default=lora]').disabled=isBusy()||!loraOverride;
    }
    selections.apply();document.body.append(root);window.addEventListener('xiantu-dhv3-function-tags-changed',refresh);document.addEventListener('keydown',onKey,true);render();q('[data-close-marks]').focus();return root;
}


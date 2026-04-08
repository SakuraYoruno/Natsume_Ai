const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=["assets/react-vendor-CHpVij2M.css"])))=>i.map(i=>d[i]);
import{a as e}from"./rolldown-runtime-COnpUsM8.js";import{n as t}from"./echarts-react-CoYsBcmM.js";import{_ as n,d as r,g as i,h as a,m as o,p as s,u as c,v as l,x as u}from"./react-vendor-CssfKxpq.js";import{$ as d,Bt as f,Ct as p,Et as ee,F as m,G as te,Ht as ne,It as re,J as ie,Lt as ae,O as oe,P as h,Rt as se,T as ce,V as le,at as ue,bt as de,et as fe,h as pe,ht as me,i as he,j as ge,l as _e,lt as ve,n as g,r as ye,t as be,v as xe,y as Se,z as Ce}from"./ui-vendor-2HIdW9ED.js";import{L as we,S as Te}from"./obs-D_cENJ3e.js";import{C as _,P as v,S as Ee,a as De,d as Oe,f as ke,i as y,k as Ae,o as b,p as je,v as Me,w as Ne,y as Pe}from"./digitalLife-DieNRWuT.js";var x=e(t(),1),Fe=e(u(),1),Ie={data:``},Le=e=>{if(typeof window==`object`){let t=(e?e.querySelector(`#_goober`):window._goober)||Object.assign(document.createElement(`style`),{innerHTML:` `,id:`_goober`});return t.nonce=window.__nonce__,t.parentNode||(e||document.head).appendChild(t),t.firstChild}return e||Ie},Re=/(?:([\u0080-\uFFFF\w-%@]+) *:? *([^{;]+?);|([^;}{]*?) *{)|(}\s*)/g,ze=/\/\*[^]*?\*\/|  +/g,S=/\n+/g,C=(e,t)=>{let n=``,r=``,i=``;for(let a in e){let o=e[a];a[0]==`@`?a[1]==`i`?n=a+` `+o+`;`:r+=a[1]==`f`?C(o,a):a+`{`+C(o,a[1]==`k`?``:t)+`}`:typeof o==`object`?r+=C(o,t?t.replace(/([^,])+/g,e=>a.replace(/([^,]*:\S+\([^)]*\))|([^,])+/g,t=>/&/.test(t)?t.replace(/&/g,e):e?e+` `+t:t)):a):o!=null&&(a=/^--/.test(a)?a:a.replace(/[A-Z]/g,`-$&`).toLowerCase(),i+=C.p?C.p(a,o):a+`:`+o+`;`)}return n+(t&&i?t+`{`+i+`}`:i)+r},w={},Be=e=>{if(typeof e==`object`){let t=``;for(let n in e)t+=n+Be(e[n]);return t}return e},Ve=(e,t,n,r,i)=>{let a=Be(e),o=w[a]||(w[a]=(e=>{let t=0,n=11;for(;t<e.length;)n=101*n+e.charCodeAt(t++)>>>0;return`go`+n})(a));if(!w[o]){let t=a===e?(e=>{let t,n,r=[{}];for(;t=Re.exec(e.replace(ze,``));)t[4]?r.shift():t[3]?(n=t[3].replace(S,` `).trim(),r.unshift(r[0][n]=r[0][n]||{})):r[0][t[1]]=t[2].replace(S,` `).trim();return r[0]})(e):e;w[o]=C(i?{[`@keyframes `+o]:t}:t,n?``:`.`+o)}let s=n&&w.g?w.g:null;return n&&(w.g=w[o]),((e,t,n,r)=>{r?t.data=t.data.replace(r,e):t.data.indexOf(e)===-1&&(t.data=n?e+t.data:t.data+e)})(w[o],t,r,s),o},He=(e,t,n)=>e.reduce((e,r,i)=>{let a=t[i];if(a&&a.call){let e=a(n),t=e&&e.props&&e.props.className||/^go/.test(e)&&e;a=t?`.`+t:e&&typeof e==`object`?e.props?``:C(e,``):!1===e?``:e}return e+r+(a??``)},``);function T(e){let t=this||{},n=e.call?e(t.p):e;return Ve(n.unshift?n.raw?He(n,[].slice.call(arguments,1),t.p):n.reduce((e,n)=>Object.assign(e,n&&n.call?n(t.p):n),{}):n,Le(t.target),t.g,t.o,t.k)}var E,D,O;T.bind({g:1});var k=T.bind({k:1});function Ue(e,t,n,r){C.p=t,E=e,D=n,O=r}function A(e,t){let n=this||{};return function(){let r=arguments;function i(a,o){let s=Object.assign({},a),c=s.className||i.className;n.p=Object.assign({theme:D&&D()},s),n.o=/ *go\d+/.test(c),s.className=T.apply(n,r)+(c?` `+c:``),t&&(s.ref=o);let l=e;return e[0]&&(l=s.as||e,delete s.as),O&&l[0]&&O(s),E(l,s)}return t?t(i):i}}var We=e=>typeof e==`function`,j=(e,t)=>We(e)?e(t):e,Ge=(()=>{let e=0;return()=>(++e).toString()})(),M=(()=>{let e;return()=>{if(e===void 0&&typeof window<`u`){let t=matchMedia(`(prefers-reduced-motion: reduce)`);e=!t||t.matches}return e}})(),Ke=20,N=`default`,P=(e,t)=>{let{toastLimit:n}=e.settings;switch(t.type){case 0:return{...e,toasts:[t.toast,...e.toasts].slice(0,n)};case 1:return{...e,toasts:e.toasts.map(e=>e.id===t.toast.id?{...e,...t.toast}:e)};case 2:let{toast:r}=t;return P(e,{type:e.toasts.find(e=>e.id===r.id)?1:0,toast:r});case 3:let{toastId:i}=t;return{...e,toasts:e.toasts.map(e=>e.id===i||i===void 0?{...e,dismissed:!0,visible:!1}:e)};case 4:return t.toastId===void 0?{...e,toasts:[]}:{...e,toasts:e.toasts.filter(e=>e.id!==t.toastId)};case 5:return{...e,pausedAt:t.time};case 6:let a=t.time-(e.pausedAt||0);return{...e,pausedAt:void 0,toasts:e.toasts.map(e=>({...e,pauseDuration:e.pauseDuration+a}))}}},F=[],I={toasts:[],pausedAt:void 0,settings:{toastLimit:Ke}},L={},qe=(e,t=N)=>{L[t]=P(L[t]||I,e),F.forEach(([e,n])=>{e===t&&n(L[t])})},Je=e=>Object.keys(L).forEach(t=>qe(e,t)),Ye=e=>Object.keys(L).find(t=>L[t].toasts.some(t=>t.id===e)),R=(e=N)=>t=>{qe(t,e)},Xe={blank:4e3,error:4e3,success:2e3,loading:1/0,custom:4e3},Ze=(e={},t=N)=>{let[n,r]=(0,x.useState)(L[t]||I),i=(0,x.useRef)(L[t]);(0,x.useEffect)(()=>(i.current!==L[t]&&r(L[t]),F.push([t,r]),()=>{let e=F.findIndex(([e])=>e===t);e>-1&&F.splice(e,1)}),[t]);let a=n.toasts.map(t=>({...e,...e[t.type],...t,removeDelay:t.removeDelay||e[t.type]?.removeDelay||e?.removeDelay,duration:t.duration||e[t.type]?.duration||e?.duration||Xe[t.type],style:{...e.style,...e[t.type]?.style,...t.style}}));return{...n,toasts:a}},Qe=(e,t=`blank`,n)=>({createdAt:Date.now(),visible:!0,dismissed:!1,type:t,ariaProps:{role:`status`,"aria-live":`polite`},message:e,pauseDuration:0,...n,id:n?.id||Ge()}),z=e=>(t,n)=>{let r=Qe(t,e,n);return R(r.toasterId||Ye(r.id))({type:2,toast:r}),r.id},B=(e,t)=>z(`blank`)(e,t);B.error=z(`error`),B.success=z(`success`),B.loading=z(`loading`),B.custom=z(`custom`),B.dismiss=(e,t)=>{let n={type:3,toastId:e};t?R(t)(n):Je(n)},B.dismissAll=e=>B.dismiss(void 0,e),B.remove=(e,t)=>{let n={type:4,toastId:e};t?R(t)(n):Je(n)},B.removeAll=e=>B.remove(void 0,e),B.promise=(e,t,n)=>{let r=B.loading(t.loading,{...n,...n?.loading});return typeof e==`function`&&(e=e()),e.then(e=>{let i=t.success?j(t.success,e):void 0;return i?B.success(i,{id:r,...n,...n?.success}):B.dismiss(r),e}).catch(e=>{let i=t.error?j(t.error,e):void 0;i?B.error(i,{id:r,...n,...n?.error}):B.dismiss(r)}),e};var $e=1e3,et=(e,t=`default`)=>{let{toasts:n,pausedAt:r}=Ze(e,t),i=(0,x.useRef)(new Map).current,a=(0,x.useCallback)((e,t=$e)=>{if(i.has(e))return;let n=setTimeout(()=>{i.delete(e),o({type:4,toastId:e})},t);i.set(e,n)},[]);(0,x.useEffect)(()=>{if(r)return;let e=Date.now(),i=n.map(n=>{if(n.duration===1/0)return;let r=(n.duration||0)+n.pauseDuration-(e-n.createdAt);if(r<0){n.visible&&B.dismiss(n.id);return}return setTimeout(()=>B.dismiss(n.id,t),r)});return()=>{i.forEach(e=>e&&clearTimeout(e))}},[n,r,t]);let o=(0,x.useCallback)(R(t),[t]),s=(0,x.useCallback)(()=>{o({type:5,time:Date.now()})},[o]),c=(0,x.useCallback)((e,t)=>{o({type:1,toast:{id:e,height:t}})},[o]),l=(0,x.useCallback)(()=>{r&&o({type:6,time:Date.now()})},[r,o]),u=(0,x.useCallback)((e,t)=>{let{reverseOrder:r=!1,gutter:i=8,defaultPosition:a}=t||{},o=n.filter(t=>(t.position||a)===(e.position||a)&&t.height),s=o.findIndex(t=>t.id===e.id),c=o.filter((e,t)=>t<s&&e.visible).length;return o.filter(e=>e.visible).slice(...r?[c+1]:[0,c]).reduce((e,t)=>e+(t.height||0)+i,0)},[n]);return(0,x.useEffect)(()=>{n.forEach(e=>{if(e.dismissed)a(e.id,e.removeDelay);else{let t=i.get(e.id);t&&(clearTimeout(t),i.delete(e.id))}})},[n,a]),{toasts:n,handlers:{updateHeight:c,startPause:s,endPause:l,calculateOffset:u}}},tt=k`
from {
  transform: scale(0) rotate(45deg);
	opacity: 0;
}
to {
 transform: scale(1) rotate(45deg);
  opacity: 1;
}`,nt=k`
from {
  transform: scale(0);
  opacity: 0;
}
to {
  transform: scale(1);
  opacity: 1;
}`,rt=k`
from {
  transform: scale(0) rotate(90deg);
	opacity: 0;
}
to {
  transform: scale(1) rotate(90deg);
	opacity: 1;
}`,it=A(`div`)`
  width: 20px;
  opacity: 0;
  height: 20px;
  border-radius: 10px;
  background: ${e=>e.primary||`#ff4b4b`};
  position: relative;
  transform: rotate(45deg);

  animation: ${tt} 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)
    forwards;
  animation-delay: 100ms;

  &:after,
  &:before {
    content: '';
    animation: ${nt} 0.15s ease-out forwards;
    animation-delay: 150ms;
    position: absolute;
    border-radius: 3px;
    opacity: 0;
    background: ${e=>e.secondary||`#fff`};
    bottom: 9px;
    left: 4px;
    height: 2px;
    width: 12px;
  }

  &:before {
    animation: ${rt} 0.15s ease-out forwards;
    animation-delay: 180ms;
    transform: rotate(90deg);
  }
`,at=k`
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
`,ot=A(`div`)`
  width: 12px;
  height: 12px;
  box-sizing: border-box;
  border: 2px solid;
  border-radius: 100%;
  border-color: ${e=>e.secondary||`#e0e0e0`};
  border-right-color: ${e=>e.primary||`#616161`};
  animation: ${at} 1s linear infinite;
`,st=k`
from {
  transform: scale(0) rotate(45deg);
	opacity: 0;
}
to {
  transform: scale(1) rotate(45deg);
	opacity: 1;
}`,ct=k`
0% {
	height: 0;
	width: 0;
	opacity: 0;
}
40% {
  height: 0;
	width: 6px;
	opacity: 1;
}
100% {
  opacity: 1;
  height: 10px;
}`,lt=A(`div`)`
  width: 20px;
  opacity: 0;
  height: 20px;
  border-radius: 10px;
  background: ${e=>e.primary||`#61d345`};
  position: relative;
  transform: rotate(45deg);

  animation: ${st} 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)
    forwards;
  animation-delay: 100ms;
  &:after {
    content: '';
    box-sizing: border-box;
    animation: ${ct} 0.2s ease-out forwards;
    opacity: 0;
    animation-delay: 200ms;
    position: absolute;
    border-right: 2px solid;
    border-bottom: 2px solid;
    border-color: ${e=>e.secondary||`#fff`};
    bottom: 6px;
    left: 6px;
    height: 10px;
    width: 6px;
  }
`,ut=A(`div`)`
  position: absolute;
`,dt=A(`div`)`
  position: relative;
  display: flex;
  justify-content: center;
  align-items: center;
  min-width: 20px;
  min-height: 20px;
`,ft=k`
from {
  transform: scale(0.6);
  opacity: 0.4;
}
to {
  transform: scale(1);
  opacity: 1;
}`,pt=A(`div`)`
  position: relative;
  transform: scale(0.6);
  opacity: 0.4;
  min-width: 20px;
  animation: ${ft} 0.3s 0.12s cubic-bezier(0.175, 0.885, 0.32, 1.275)
    forwards;
`,mt=({toast:e})=>{let{icon:t,type:n,iconTheme:r}=e;return t===void 0?n===`blank`?null:x.createElement(dt,null,x.createElement(ot,{...r}),n!==`loading`&&x.createElement(ut,null,n===`error`?x.createElement(it,{...r}):x.createElement(lt,{...r}))):typeof t==`string`?x.createElement(pt,null,t):t},ht=e=>`
0% {transform: translate3d(0,${e*-200}%,0) scale(.6); opacity:.5;}
100% {transform: translate3d(0,0,0) scale(1); opacity:1;}
`,gt=e=>`
0% {transform: translate3d(0,0,-1px) scale(1); opacity:1;}
100% {transform: translate3d(0,${e*-150}%,-1px) scale(.6); opacity:0;}
`,_t=`0%{opacity:0;} 100%{opacity:1;}`,vt=`0%{opacity:1;} 100%{opacity:0;}`,yt=A(`div`)`
  display: flex;
  align-items: center;
  background: #fff;
  color: #363636;
  line-height: 1.3;
  will-change: transform;
  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.1), 0 3px 3px rgba(0, 0, 0, 0.05);
  max-width: 350px;
  pointer-events: auto;
  padding: 8px 10px;
  border-radius: 8px;
`,bt=A(`div`)`
  display: flex;
  justify-content: center;
  margin: 4px 10px;
  color: inherit;
  flex: 1 1 auto;
  white-space: pre-line;
`,xt=(e,t)=>{let n=e.includes(`top`)?1:-1,[r,i]=M()?[_t,vt]:[ht(n),gt(n)];return{animation:t?`${k(r)} 0.35s cubic-bezier(.21,1.02,.73,1) forwards`:`${k(i)} 0.4s forwards cubic-bezier(.06,.71,.55,1)`}},St=x.memo(({toast:e,position:t,style:n,children:r})=>{let i=e.height?xt(e.position||t||`top-center`,e.visible):{opacity:0},a=x.createElement(mt,{toast:e}),o=x.createElement(bt,{...e.ariaProps},j(e.message,e));return x.createElement(yt,{className:e.className,style:{...i,...n,...e.style}},typeof r==`function`?r({icon:a,message:o}):x.createElement(x.Fragment,null,a,o))});Ue(x.createElement);var Ct=({id:e,className:t,style:n,onHeightUpdate:r,children:i})=>{let a=x.useCallback(t=>{if(t){let n=()=>{let n=t.getBoundingClientRect().height;r(e,n)};n(),new MutationObserver(n).observe(t,{subtree:!0,childList:!0,characterData:!0})}},[e,r]);return x.createElement(`div`,{ref:a,className:t,style:n},i)},wt=(e,t)=>{let n=e.includes(`top`),r=n?{top:0}:{bottom:0},i=e.includes(`center`)?{justifyContent:`center`}:e.includes(`right`)?{justifyContent:`flex-end`}:{};return{left:0,right:0,display:`flex`,position:`absolute`,transition:M()?void 0:`all 230ms cubic-bezier(.21,1.02,.73,1)`,transform:`translateY(${t*(n?1:-1)}px)`,...r,...i}},Tt=T`
  z-index: 9999;
  > * {
    pointer-events: auto;
  }
`,V=16,Et=({reverseOrder:e,position:t=`top-center`,toastOptions:n,gutter:r,children:i,toasterId:a,containerStyle:o,containerClassName:s})=>{let{toasts:c,handlers:l}=et(n,a);return x.createElement(`div`,{"data-rht-toaster":a||``,style:{position:`fixed`,zIndex:9999,top:V,left:V,right:V,bottom:V,pointerEvents:`none`,...o},className:s,onMouseEnter:l.startPause,onMouseLeave:l.endPause},c.map(n=>{let a=n.position||t,o=wt(a,l.calculateOffset(n,{reverseOrder:e,gutter:r,defaultPosition:t}));return x.createElement(Ct,{id:n.id,key:n.id,onHeightUpdate:l.updateHeight,className:n.visible?Tt:``,style:o},n.type===`custom`?j(n.message,n):i?i(n):x.createElement(St,{toast:n,position:a}))}))},Dt=B,H=`__monitor_ui_chunk_reload_build__`;function Ot(){return import.meta?.url?String(import.meta.url):typeof window<`u`?document.querySelector(`script[type="module"][src*="/monitor/assets/index-"]`)?.getAttribute(`src`)||window.location.pathname:`unknown-build`}function U(e){let t=String(e?.message||e?.reason?.message||e?.payload?.message||e||``).toLowerCase();return t.includes(`failed to fetch dynamically imported module`)||t.includes(`error loading dynamically imported module`)||t.includes(`importing a module script failed`)||t.includes(`unable to preload css`)}function W(){if(typeof window>`u`)return!1;let e=Ot();try{if(sessionStorage.getItem(H)===e)return!1;sessionStorage.setItem(H,e)}catch{}return window.location.reload(),!0}function kt(){typeof window>`u`||(window.addEventListener(`vite:preloadError`,e=>{U(e)&&(e.preventDefault(),W())}),window.addEventListener(`error`,e=>{U(e?.error||e?.message||e)&&W()}),window.addEventListener(`unhandledrejection`,e=>{U(e?.reason||e)&&W()}))}var G=c(),At=class extends x.Component{constructor(e){super(e),this.state={hasError:!1,error:null,errorInfo:null}}static getDerivedStateFromError(e){return{hasError:!0,error:e}}componentDidCatch(e,t){this.setState({errorInfo:t}),console.error(`[ErrorBoundary] Uncaught error:`,e,t),U(e)&&W()}handleReset=()=>{this.setState({hasError:!1,error:null,errorInfo:null})};render(){return this.state.hasError?(0,G.jsx)(`div`,{className:`min-h-screen flex items-center justify-center bg-gray-50 p-8`,children:(0,G.jsxs)(`div`,{className:`max-w-lg w-full text-center space-y-6`,children:[(0,G.jsx)(`div`,{className:`flex justify-center`,children:(0,G.jsx)(`span`,{className:`flex items-center justify-center w-16 h-16 rounded-2xl bg-red-100 text-red-500`,children:(0,G.jsx)(pe,{size:32})})}),(0,G.jsxs)(`div`,{children:[(0,G.jsx)(`h1`,{className:`text-xl font-semibold text-gray-900`,children:`页面渲染出错`}),(0,G.jsx)(`p`,{className:`mt-2 text-sm text-gray-500`,children:`监控面板遇到了一个意外错误。你可以尝试重新加载当前页面。`})]}),this.state.error&&(0,G.jsx)(`pre`,{className:`text-left rounded-lg border border-red-200 bg-red-50 p-4 text-xs text-red-700 overflow-auto max-h-40`,children:String(this.state.error)}),(0,G.jsxs)(`div`,{className:`flex items-center justify-center gap-3`,children:[(0,G.jsxs)(`button`,{className:`inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary-600 transition-colors`,onClick:this.handleReset,children:[(0,G.jsx)(h,{size:14}),`重试当前页面`]}),(0,G.jsx)(`button`,{className:`inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-gray-300 bg-white text-gray-700 text-sm font-medium hover:bg-gray-50 transition-colors`,onClick:()=>window.location.reload(),children:`刷新整个应用`})]})]})}):this.props.children}},K=be(e=>({pollingEnabled:!0,pollingInterval:30,setPollingEnabled:t=>e({pollingEnabled:t}),setPollingInterval:t=>e({pollingInterval:t}),timeRange:`1h`,setTimeRange:t=>e({timeRange:t}),selectedRequestId:null,setSelectedRequestId:t=>e({selectedRequestId:t})}));function q(e={}){let{windowMinutes:t=60,pollingEnabled:n=!1,pollingInterval:r=15,minIntervalSeconds:i=15}=e,[a,o]=(0,x.useState)(null),[s,c]=(0,x.useState)(null),[l,u]=(0,x.useState)(!0),[d,f]=(0,x.useState)(null),p=(0,x.useCallback)(async(e={cancelled:!1})=>{u(!0),f(null);try{let[n,r]=await Promise.allSettled([Te(),we({window_minutes:t})]);if(e.cancelled)return;n.status===`fulfilled`?o(n.value):f(n.reason?.message||`runtime snapshot load failed`),r.status===`fulfilled`&&c(r.value)}finally{e.cancelled||u(!1)}},[t]);return(0,x.useEffect)(()=>{let e={cancelled:!1};if(p(e).catch(()=>{}),!n)return()=>{e.cancelled=!0};let t=setInterval(()=>{p(e).catch(()=>{})},Math.max(r,i)*1e3);return()=>{e.cancelled=!0,clearInterval(t)}},[p,n,r,i]),{runtimeSnapshot:a,promptStats:s,loading:l,error:d,reload:(0,x.useCallback)(async()=>{await p({cancelled:!1})},[p])}}var J={critical:0,warning:1,info:2};function jt({mode:e,bondStage:t,replyPosture:n,dominantMotive:r,absenceBand:i,dominantMotiveRaw:a,absenceBandRaw:o}){let s=[`当前处于${e}`],c=v(a).toLowerCase(),l=v(o).toLowerCase();return t&&t!==`—`&&s.push(`关系阶段为${t}`),n&&n!==`—`&&s.push(`回复姿态是${n}`),r&&r!==`—`&&c&&c!==`none`?s.push(`当前主导动机为${r}`):i&&i!==`—`&&l&&l!==`recent`&&s.push(`当前处于${i}`),`${s.join(`，`)}。`}function Y(e){return Object.entries(e?.slot_status||{}).filter(([,e])=>e!==`live`)}function X(e,t={}){let n=t.promptStats||null,r=[],i=e?.timestamp||Date.now();if(!e)return r;let a=e.sys_mode_status||{},o=e.self_reflection_pulse?.narrative_guardrails||{},s=e.silence_state||{},c=e.response_policy||{},l=e.runtime_summary||{},u=e.degraded_reasons||[],d=Y(e);if(a.degraded||a.degraded_reason){let e=v(a.degraded_reason);r.push({reason_code:`RUNTIME_MODE_DEGRADED`,title:b(`RUNTIME_MODE_DEGRADED`),detail:e?`存在状态轴当前处于降级状态：${e}`:`存在状态轴当前处于降级状态。`,ts_ms:i,severity:`warning`,source:`runtime`,to:`/runtime-mode`})}if(u.length>0){let e=u.map(Oe).filter(Boolean).join(`；`);r.push({reason_code:`RUNTIME_SNAPSHOT_DEGRADED`,title:b(`RUNTIME_SNAPSHOT_DEGRADED`),detail:y(e||`运行快照中仍有关键状态未补齐。`,180),ts_ms:i,severity:`warning`,source:`runtime`,to:`/digital-life`})}d.length>0&&r.push({reason_code:`RUNTIME_SLOT_NOT_LIVE`,title:b(`RUNTIME_SLOT_NOT_LIVE`),detail:y(`共有 ${d.length} 个关键槽位未在线：${Pe(d)}`,180),ts_ms:i,severity:`warning`,source:`runtime`,to:`/digital-life`});let f=String(o.risk_level||``).toLowerCase();if(f&&f!==`low`&&f!==`none`){let e=Array.isArray(o.risk_tags)?o.risk_tags.map(ke).filter(Boolean):[];r.push({reason_code:`REFLECTION_GUARD_ACTIVE`,title:b(`REFLECTION_GUARD_ACTIVE`),detail:y(`当前自我反思护栏风险为${_(f)}${e.length?`，涉及 ${e.join(`、`)}`:``}。`,180),ts_ms:i,severity:f===`high`||f===`critical`?`critical`:`warning`,source:`runtime`,to:`/self-model`})}s.topic_resistance_active&&r.push({reason_code:`TOPIC_RESISTANCE_ACTIVE`,title:b(`TOPIC_RESISTANCE_ACTIVE`),detail:`当前对“${s.resistant_topic||`该话题`}”存在明显抗拒，回复会更偏谨慎。`,ts_ms:i,severity:`warning`,source:`runtime`,to:`/response-policy`}),c.should_be_withdrawn&&r.push({reason_code:`WITHDRAWN_REPLY_POLICY`,title:b(`WITHDRAWN_REPLY_POLICY`),detail:`当前策略更偏短答与收敛，回复姿态为${Ee(l.reply_posture?.style)}。`,ts_ms:i,severity:`info`,source:`runtime`,to:`/response-policy`}),(n?.injection_detected_count??0)>0&&r.push({reason_code:`PROMPT_INJECTION_DETECTED`,title:b(`PROMPT_INJECTION_DETECTED`),detail:`当前时间窗内检测到 ${n.injection_detected_count} 次提示注入风险。`,ts_ms:i,severity:`critical`,source:`prompt`,to:`/prompt-config`}),(n?.high_risk_count??0)>0&&r.push({reason_code:`PROMPT_HIGH_RISK_BUILD`,title:b(`PROMPT_HIGH_RISK_BUILD`),detail:`当前时间窗内存在 ${n.high_risk_count} 次高风险 Prompt 构建。`,ts_ms:i,severity:`warning`,source:`prompt`,to:`/prompt-config`});let p=e.background_runtime||{};if(p&&typeof p==`object`&&Object.keys(p).length>0){let e=String(p.scheduler_status||`UNKNOWN`).toUpperCase(),t=String(p.tick_health||`unknown`).toLowerCase(),n=!!p.is_advancing,a=p.seconds_since_inner_world_tick,o=Array.isArray(p.core_ticks_missing)?p.core_ticks_missing.filter(Boolean):[];n||r.push({reason_code:`BACKGROUND_RUNTIME_NOT_ADVANCING`,title:b(`BACKGROUND_RUNTIME_NOT_ADVANCING`),detail:y(`后台推进未达预期：tick=${t||`unknown`}，scheduler=${e}，inner_world_tick 距今 ${a??`unknown`}s。`,180),ts_ms:i,severity:t===`stalled`?`critical`:`warning`,source:`runtime`,to:`/inner-world`}),o.length>0&&r.push({reason_code:`BACKGROUND_CORE_TICK_MISSING`,title:b(`BACKGROUND_CORE_TICK_MISSING`),detail:y(`后台核心任务缺失：${o.join(`、`)}。`,180),ts_ms:i,severity:`warning`,source:`runtime`,to:`/automation`}),p.persona_user_alignment_ok===!1&&r.push({reason_code:`BACKGROUND_PERSONA_MISMATCH`,title:b(`BACKGROUND_PERSONA_MISMATCH`),detail:y(`内在世界 user_id 与当前人格 user_id 未对齐，后台推进可能写入错误用户。`,180),ts_ms:i,severity:`critical`,source:`runtime`,to:`/inner-world`})}return r.sort((e,t)=>{let n=(J[e.severity]??99)-(J[t.severity]??99);return n===0?Number(t.ts_ms||0)-Number(e.ts_ms||0):n})}function Mt(e){let t=e?.runtime_summary||{},n=e?.motive_state?.dominant_motive,r=e?.offline_state?.absence_band||t?.current_state?.absence_band,i=Me(e?.sys_mode),a=Ae(e?.bond_state?.current_stage),o=Ee(t.reply_posture?.style),s=je(n),c=_(e?.self_reflection_pulse?.narrative_guardrails?.risk_level),l=De(r);return{mode:i,bondStage:a,replyPosture:o,dominantMotive:s,riskLevel:c,absenceBand:l,headline:jt({mode:i,bondStage:a,replyPosture:o,dominantMotive:s,absenceBand:l,dominantMotiveRaw:n,absenceBandRaw:r}),notableSignals:(t.notable_signals||[]).map(Ne).filter(Boolean)}}var Nt=[{title:`数字生命`,items:[{to:`/digital-life`,icon:g,label:`总览`},{to:`/emotion`,icon:Se,label:`情绪`},{to:`/bond`,icon:ue,label:`关系`},{to:`/memory`,icon:de,label:`记忆`},{to:`/runtime-mode`,icon:g,label:`存在态`},{to:`/inner-world`,icon:le,label:`内在世界`},{to:`/response-policy`,icon:xe,label:`回复策略`},{to:`/self-model`,icon:ae,label:`自我模型`}]},{title:`运营`,items:[{to:`/prompt-config`,icon:me,label:`提示词`},{to:`/persona-config`,icon:_e,label:`人格文案`},{to:`/system`,icon:ce,label:`系统底座`},{to:`/canon`,icon:f,label:`设定管理`}]},{title:`观测`,items:[{to:`/overview`,icon:fe,label:`运行总览`},{to:`/sessions`,icon:te,label:`会话`},{to:`/trace`,icon:ve,label:`链路`},{to:`/metrics`,icon:re,label:`指标`},{to:`/insights`,icon:d,label:`洞察`},{to:`/chat-debug`,icon:oe,label:`Chat 调试`},{to:`/alerts`,icon:ne,label:`告警`},{to:`/logs`,icon:ge,label:`日志`}]},{title:`基础设施`,items:[{to:`/gateway`,icon:m,label:`网关`},{to:`/skills`,icon:Ce,label:`技能`},{to:`/tools`,icon:he,label:`工具`},{to:`/automation`,icon:p,label:`自动化`},{to:`/agent`,icon:se,label:`Agent Loop`}]}],Pt=[15,30,60],Ft={critical:`border-red-200 bg-red-50 text-red-700`,warning:`border-orange-200 bg-orange-50 text-orange-700`,info:`border-blue-200 bg-blue-50 text-blue-700`};function Z({value:e,severity:t=`info`}){return e?(0,G.jsx)(`span`,{className:`inline-flex min-w-5 items-center justify-center rounded-full border px-1.5 py-0.5 text-[11px] font-semibold ${Ft[t]||Ft.info}`,children:e}):null}function It({to:e,icon:t,label:n,onClick:r,badge:i=null}){return(0,G.jsxs)(s,{to:e,onClick:r,className:({isActive:e})=>`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${e?`bg-primary-50 text-primary`:`text-gray-600 hover:bg-gray-100 hover:text-gray-800`}`,children:[(0,G.jsx)(t,{size:15}),n,i?(0,G.jsx)(Z,{value:i.value,severity:i.severity}):null]})}function Lt(){let e=K(e=>e.pollingEnabled),t=K(e=>e.pollingInterval),n=K(e=>e.setPollingEnabled),r=K(e=>e.setPollingInterval),[i,o]=(0,x.useState)(!1),s=l(),{runtimeSnapshot:c,promptStats:u}=q({pollingEnabled:e,pollingInterval:t,windowMinutes:60}),d=X(c,{promptStats:u});(0,x.useEffect)(()=>{o(!1)},[s.pathname]);let f=d.length>0?{value:d.length,severity:d[0]?.severity||`info`}:null,p={数字生命:f},m={"/alerts":f};return(0,G.jsxs)(`div`,{className:`flex h-full flex-col bg-gray-50`,children:[(0,G.jsx)(`header`,{className:`sticky top-0 z-50 border-b border-gray-200 bg-white/95 shadow-sm backdrop-blur`,children:(0,G.jsxs)(`div`,{className:`mx-auto w-full space-y-3 px-4 py-3 sm:px-6`,children:[(0,G.jsxs)(`div`,{className:`flex items-center justify-between gap-4`,children:[(0,G.jsxs)(`div`,{className:`flex items-center gap-2`,children:[(0,G.jsx)(`button`,{className:`rounded p-1.5 text-gray-500 transition-colors hover:bg-gray-100 lg:hidden`,onClick:()=>o(e=>!e),"aria-label":`切换导航菜单`,children:i?(0,G.jsx)(ye,{size:20}):(0,G.jsx)(ie,{size:20})}),(0,G.jsxs)(`div`,{children:[(0,G.jsx)(`div`,{className:`text-lg font-bold tracking-tight text-primary`,children:`Natsume AI`}),(0,G.jsx)(`div`,{className:`hidden text-xs text-gray-400 sm:block`,children:`数字生命控制台`})]})]}),(0,G.jsxs)(`div`,{className:`flex items-center gap-2 text-sm`,children:[(0,G.jsxs)(`button`,{className:`flex items-center gap-1.5 rounded border px-2.5 py-1.5 text-xs font-medium transition-colors ${e?`border-green-300 bg-green-50 text-green-700 hover:bg-green-100`:`border-gray-300 bg-gray-50 text-gray-500 hover:bg-gray-100`}`,onClick:()=>n(!e),title:e?`点击暂停自动刷新`:`点击开启自动刷新`,children:[e?(0,G.jsx)(h,{size:12,className:`animate-spin`}):(0,G.jsx)(ee,{size:12}),e?`轮询中`:`已暂停`]}),(0,G.jsx)(`select`,{className:`rounded border border-gray-300 px-1.5 py-1.5 text-xs text-gray-600 focus:outline-none focus:ring-1 focus:ring-primary-200`,value:t,onChange:e=>r(Number(e.target.value)),title:`自动刷新间隔`,children:Pt.map(e=>(0,G.jsxs)(`option`,{value:e,children:[e,`s`]},e))})]})]}),(0,G.jsx)(`nav`,{className:`hidden flex-wrap gap-3 lg:flex`,role:`navigation`,"aria-label":`主导航`,children:Nt.map(e=>(0,G.jsxs)(`div`,{className:`rounded-xl border border-gray-200 bg-gray-50 px-2.5 py-2`,children:[(0,G.jsxs)(`div`,{className:`flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400`,children:[(0,G.jsx)(`span`,{children:e.title}),p[e.title]?(0,G.jsx)(Z,{value:p[e.title].value,severity:p[e.title].severity}):null]}),(0,G.jsx)(`div`,{className:`mt-1 flex flex-wrap gap-1`,children:e.items.map(e=>(0,G.jsx)(It,{...e,badge:m[e.to]},e.to))})]},e.title))})]})}),i?(0,G.jsxs)(G.Fragment,{children:[(0,G.jsx)(`div`,{className:`fixed inset-0 z-40 bg-black/30 lg:hidden`,onClick:()=>o(!1)}),(0,G.jsx)(`nav`,{className:`fixed bottom-0 left-0 top-[72px] z-50 w-72 overflow-y-auto border-r border-gray-200 bg-white shadow-xl lg:hidden`,role:`navigation`,"aria-label":`移动端导航`,children:(0,G.jsx)(`div`,{className:`space-y-3 p-3`,children:Nt.map(e=>(0,G.jsxs)(`div`,{className:`rounded-xl border border-gray-200 bg-gray-50 p-2`,children:[(0,G.jsxs)(`div`,{className:`flex items-center gap-1.5 px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400`,children:[(0,G.jsx)(`span`,{children:e.title}),p[e.title]?(0,G.jsx)(Z,{value:p[e.title].value,severity:p[e.title].severity}):null]}),(0,G.jsx)(`div`,{className:`space-y-1`,children:e.items.map(e=>(0,G.jsx)(It,{...e,badge:m[e.to],onClick:()=>o(!1)},e.to))})]},e.title))})})]}):null,(0,G.jsx)(`main`,{className:`mx-auto flex w-full min-w-0 flex-1 flex-col px-4 py-6 sm:px-6 lg:px-8`,children:(0,G.jsx)(a,{})})]})}var Rt=(function(){let e=typeof document<`u`&&document.createElement(`link`).relList;return e&&e.supports&&e.supports(`modulepreload`)?`modulepreload`:`preload`})(),zt=function(e){return`/monitor/`+e},Bt={},Q=function(e,t,n){let r=Promise.resolve();if(t&&t.length>0){let e=document.getElementsByTagName(`link`),i=document.querySelector(`meta[property=csp-nonce]`),a=i?.nonce||i?.getAttribute(`nonce`);function o(e){return Promise.all(e.map(e=>Promise.resolve(e).then(e=>({status:`fulfilled`,value:e}),e=>({status:`rejected`,reason:e}))))}r=o(t.map(t=>{if(t=zt(t,n),t in Bt)return;Bt[t]=!0;let r=t.endsWith(`.css`),i=r?`[rel="stylesheet"]`:``;if(n)for(let n=e.length-1;n>=0;n--){let i=e[n];if(i.href===t&&(!r||i.rel===`stylesheet`))return}else if(document.querySelector(`link[href="${t}"]${i}`))return;let o=document.createElement(`link`);if(o.rel=r?`stylesheet`:Rt,r||(o.as=`script`),o.crossOrigin=``,o.href=t,a&&o.setAttribute(`nonce`,a),document.head.appendChild(o),r)return new Promise((e,n)=>{o.addEventListener(`load`,e),o.addEventListener(`error`,()=>n(Error(`Unable to preload CSS for ${t}`)))})}))}function i(e){let t=new Event(`vite:preloadError`,{cancelable:!0});if(t.payload=e,window.dispatchEvent(t),!t.defaultPrevented)throw e}return r.then(t=>{for(let e of t||[])e.status===`rejected`&&i(e.reason);return e().catch(i)})},Vt=(0,x.lazy)(()=>Q(()=>import(`./DigitalLifeOverview-DyowKrd8.js`),__vite__mapDeps([0]))),Ht=(0,x.lazy)(()=>Q(()=>import(`./Overview-BPEGRHzy.js`),__vite__mapDeps([0]))),Ut=(0,x.lazy)(()=>Q(()=>import(`./Sessions-BssOeXIB.js`),__vite__mapDeps([0]))),Wt=(0,x.lazy)(()=>Q(()=>import(`./Trace-DQDm_M8_.js`),__vite__mapDeps([0]))),Gt=(0,x.lazy)(()=>Q(()=>import(`./Metrics-DBuv6jDt.js`),__vite__mapDeps([0]))),Kt=(0,x.lazy)(()=>Q(()=>import(`./Insights-CS9amxg5.js`),__vite__mapDeps([0]))),qt=(0,x.lazy)(()=>Q(()=>import(`./Canon-PtPP3CvH.js`),__vite__mapDeps([0]))),Jt=(0,x.lazy)(()=>Q(()=>import(`./Bond-BxkdJ-Qx.js`),__vite__mapDeps([0]))),Yt=(0,x.lazy)(()=>Q(()=>import(`./Tools-CljHC8kp.js`),__vite__mapDeps([0]))),Xt=(0,x.lazy)(()=>Q(()=>import(`./Automation-BGYHEY4l.js`),__vite__mapDeps([0]))),Zt=(0,x.lazy)(()=>Q(()=>import(`./AgentLoop-DQgF9PFO.js`),__vite__mapDeps([0]))),Qt=(0,x.lazy)(()=>Q(()=>import(`./Gateway-Cb9UNmt3.js`),__vite__mapDeps([0]))),$t=(0,x.lazy)(()=>Q(()=>import(`./SkillsHub-BD3xRiFf.js`),__vite__mapDeps([0]))),en=(0,x.lazy)(()=>Q(()=>import(`./ChatDebug-ADTKyvJm.js`),__vite__mapDeps([0]))),tn=(0,x.lazy)(()=>Q(()=>import(`./Alerts-C6otbkc2.js`),__vite__mapDeps([0]))),nn=(0,x.lazy)(()=>Q(()=>import(`./LogStream-CFyfG6OB.js`),__vite__mapDeps([0]))),rn=(0,x.lazy)(()=>Q(()=>import(`./EmotionDashboard-JMsFvlw8.js`),__vite__mapDeps([0]))),an=(0,x.lazy)(()=>Q(()=>import(`./MemoryBrowser-B51swDLx.js`),__vite__mapDeps([0]))),on=(0,x.lazy)(()=>Q(()=>import(`./SystemConfig-CTqLAzYZ.js`),__vite__mapDeps([0]))),sn=(0,x.lazy)(()=>Q(()=>import(`./RuntimeMode-BwQGaDX-.js`),__vite__mapDeps([0]))),cn=(0,x.lazy)(()=>Q(()=>import(`./PromptConfig-3WBVhoMd.js`),__vite__mapDeps([0]))),ln=(0,x.lazy)(()=>Q(()=>import(`./PersonaPromptOps-Bmfedt0N.js`),__vite__mapDeps([0]))),un=(0,x.lazy)(()=>Q(()=>import(`./InnerWorldDashboard-CenxRiXP.js`),__vite__mapDeps([0]))),dn=(0,x.lazy)(()=>Q(()=>import(`./ResponsePolicyDashboard-fgqAFLPE.js`),__vite__mapDeps([0]))),fn=(0,x.lazy)(()=>Q(()=>import(`./SelfModelDashboard-BWiI4bM9.js`),__vite__mapDeps([0])));function pn(){return(0,G.jsx)(`div`,{className:`rounded-2xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500 shadow-sm`,children:`正在加载页面…`})}function $(e){return(0,G.jsx)(x.Suspense,{fallback:(0,G.jsx)(pn,{}),children:e})}function mn(){return(0,G.jsx)(At,{children:(0,G.jsxs)(r,{children:[(0,G.jsx)(n,{children:(0,G.jsxs)(i,{path:`/`,element:(0,G.jsx)(Lt,{}),children:[(0,G.jsx)(i,{index:!0,element:(0,G.jsx)(o,{to:`/digital-life`,replace:!0})}),(0,G.jsx)(i,{path:`digital-life`,element:$((0,G.jsx)(Vt,{}))}),(0,G.jsx)(i,{path:`overview`,element:$((0,G.jsx)(Ht,{}))}),(0,G.jsx)(i,{path:`sessions`,element:$((0,G.jsx)(Ut,{}))}),(0,G.jsx)(i,{path:`trace/*`,element:$((0,G.jsx)(Wt,{}))}),(0,G.jsx)(i,{path:`metrics`,element:$((0,G.jsx)(Gt,{}))}),(0,G.jsx)(i,{path:`insights/*`,element:$((0,G.jsx)(Kt,{}))}),(0,G.jsx)(i,{path:`chat-debug`,element:$((0,G.jsx)(en,{}))}),(0,G.jsx)(i,{path:`alerts`,element:$((0,G.jsx)(tn,{}))}),(0,G.jsx)(i,{path:`logs`,element:$((0,G.jsx)(nn,{}))}),(0,G.jsx)(i,{path:`emotion`,element:$((0,G.jsx)(rn,{}))}),(0,G.jsx)(i,{path:`memory`,element:$((0,G.jsx)(an,{}))}),(0,G.jsx)(i,{path:`system`,element:$((0,G.jsx)(on,{}))}),(0,G.jsx)(i,{path:`prompt-config`,element:$((0,G.jsx)(cn,{}))}),(0,G.jsx)(i,{path:`persona-config`,element:$((0,G.jsx)(ln,{}))}),(0,G.jsx)(i,{path:`runtime-mode`,element:$((0,G.jsx)(sn,{}))}),(0,G.jsx)(i,{path:`inner-world`,element:$((0,G.jsx)(un,{}))}),(0,G.jsx)(i,{path:`response-policy`,element:$((0,G.jsx)(dn,{}))}),(0,G.jsx)(i,{path:`self-model`,element:$((0,G.jsx)(fn,{}))}),(0,G.jsx)(i,{path:`canon`,element:$((0,G.jsx)(qt,{}))}),(0,G.jsx)(i,{path:`bond`,element:$((0,G.jsx)(Jt,{}))}),(0,G.jsx)(i,{path:`tools`,element:$((0,G.jsx)(Yt,{}))}),(0,G.jsx)(i,{path:`automation`,element:$((0,G.jsx)(Xt,{}))}),(0,G.jsx)(i,{path:`agent`,element:$((0,G.jsx)(Zt,{}))}),(0,G.jsx)(i,{path:`gateway`,element:$((0,G.jsx)(Qt,{}))}),(0,G.jsx)(i,{path:`skills`,element:$((0,G.jsx)($t,{}))}),(0,G.jsx)(i,{path:`wechat`,element:(0,G.jsx)(o,{to:`/gateway`,replace:!0})})]})}),(0,G.jsx)(Et,{position:`top-right`,toastOptions:{duration:4e3,style:{fontSize:`13px`}}})]})})}kt(),Fe.createRoot(document.getElementById(`root`)).render((0,G.jsx)(x.StrictMode,{children:(0,G.jsx)(mn,{})}));export{q as a,Y as i,X as n,K as o,Mt as r,Dt as s,Q as t};
const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=["assets/react-vendor-CHpVij2M.css"])))=>i.map(i=>d[i]);
import{a as e}from"./rolldown-runtime-COnpUsM8.js";import{n as t}from"./echarts-react-CoYsBcmM.js";import{S as n,_ as r,d as i,g as a,h as o,p as s,u as c,v as l,y as u}from"./react-vendor-BIpP6WD3.js";import{$ as d,Dt as f,F as p,G as m,J as h,Lt as g,O as _,P as v,Rt as y,T as b,Ut as x,V as S,Vt as ee,at as te,et as ne,gt as re,h as ie,i as ae,j as oe,l as se,lt as ce,n as le,r as ue,t as de,v as fe,wt as pe,xt as me,y as he,z as ge,zt as _e}from"./ui-vendor-Dvnkz5ln.js";import{S as ve,z as ye}from"./obs-D_COwm9n.js";import{C as be,P as C,S as w,a as xe,d as Se,f as Ce,i as T,k as we,o as E,p as Te,v as Ee,w as De,y as Oe}from"./digitalLife-81e1huoS.js";var D=e(t(),1),ke=e(n(),1),Ae={data:``},je=e=>{if(typeof window==`object`){let t=(e?e.querySelector(`#_goober`):window._goober)||Object.assign(document.createElement(`style`),{innerHTML:` `,id:`_goober`});return t.nonce=window.__nonce__,t.parentNode||(e||document.head).appendChild(t),t.firstChild}return e||Ae},Me=/(?:([\u0080-\uFFFF\w-%@]+) *:? *([^{;]+?);|([^;}{]*?) *{)|(}\s*)/g,Ne=/\/\*[^]*?\*\/|  +/g,Pe=/\n+/g,O=(e,t)=>{let n=``,r=``,i=``;for(let a in e){let o=e[a];a[0]==`@`?a[1]==`i`?n=a+` `+o+`;`:r+=a[1]==`f`?O(o,a):a+`{`+O(o,a[1]==`k`?``:t)+`}`:typeof o==`object`?r+=O(o,t?t.replace(/([^,])+/g,e=>a.replace(/([^,]*:\S+\([^)]*\))|([^,])+/g,t=>/&/.test(t)?t.replace(/&/g,e):e?e+` `+t:t)):a):o!=null&&(a=/^--/.test(a)?a:a.replace(/[A-Z]/g,`-$&`).toLowerCase(),i+=O.p?O.p(a,o):a+`:`+o+`;`)}return n+(t&&i?t+`{`+i+`}`:i)+r},k={},A=e=>{if(typeof e==`object`){let t=``;for(let n in e)t+=n+A(e[n]);return t}return e},Fe=(e,t,n,r,i)=>{let a=A(e),o=k[a]||(k[a]=(e=>{let t=0,n=11;for(;t<e.length;)n=101*n+e.charCodeAt(t++)>>>0;return`go`+n})(a));if(!k[o]){let t=a===e?(e=>{let t,n,r=[{}];for(;t=Me.exec(e.replace(Ne,``));)t[4]?r.shift():t[3]?(n=t[3].replace(Pe,` `).trim(),r.unshift(r[0][n]=r[0][n]||{})):r[0][t[1]]=t[2].replace(Pe,` `).trim();return r[0]})(e):e;k[o]=O(i?{[`@keyframes `+o]:t}:t,n?``:`.`+o)}let s=n&&k.g?k.g:null;return n&&(k.g=k[o]),((e,t,n,r)=>{r?t.data=t.data.replace(r,e):t.data.indexOf(e)===-1&&(t.data=n?e+t.data:t.data+e)})(k[o],t,r,s),o},Ie=(e,t,n)=>e.reduce((e,r,i)=>{let a=t[i];if(a&&a.call){let e=a(n),t=e&&e.props&&e.props.className||/^go/.test(e)&&e;a=t?`.`+t:e&&typeof e==`object`?e.props?``:O(e,``):!1===e?``:e}return e+r+(a??``)},``);function j(e){let t=this||{},n=e.call?e(t.p):e;return Fe(n.unshift?n.raw?Ie(n,[].slice.call(arguments,1),t.p):n.reduce((e,n)=>Object.assign(e,n&&n.call?n(t.p):n),{}):n,je(t.target),t.g,t.o,t.k)}var Le,M,N;j.bind({g:1});var P=j.bind({k:1});function Re(e,t,n,r){O.p=t,Le=e,M=n,N=r}function F(e,t){let n=this||{};return function(){let r=arguments;function i(a,o){let s=Object.assign({},a),c=s.className||i.className;n.p=Object.assign({theme:M&&M()},s),n.o=/ *go\d+/.test(c),s.className=j.apply(n,r)+(c?` `+c:``),t&&(s.ref=o);let l=e;return e[0]&&(l=s.as||e,delete s.as),N&&l[0]&&N(s),Le(l,s)}return t?t(i):i}}var ze=e=>typeof e==`function`,I=(e,t)=>ze(e)?e(t):e,Be=(()=>{let e=0;return()=>(++e).toString()})(),Ve=(()=>{let e;return()=>{if(e===void 0&&typeof window<`u`){let t=matchMedia(`(prefers-reduced-motion: reduce)`);e=!t||t.matches}return e}})(),He=20,L=`default`,Ue=(e,t)=>{let{toastLimit:n}=e.settings;switch(t.type){case 0:return{...e,toasts:[t.toast,...e.toasts].slice(0,n)};case 1:return{...e,toasts:e.toasts.map(e=>e.id===t.toast.id?{...e,...t.toast}:e)};case 2:let{toast:r}=t;return Ue(e,{type:e.toasts.find(e=>e.id===r.id)?1:0,toast:r});case 3:let{toastId:i}=t;return{...e,toasts:e.toasts.map(e=>e.id===i||i===void 0?{...e,dismissed:!0,visible:!1}:e)};case 4:return t.toastId===void 0?{...e,toasts:[]}:{...e,toasts:e.toasts.filter(e=>e.id!==t.toastId)};case 5:return{...e,pausedAt:t.time};case 6:let a=t.time-(e.pausedAt||0);return{...e,pausedAt:void 0,toasts:e.toasts.map(e=>({...e,pauseDuration:e.pauseDuration+a}))}}},R=[],z={toasts:[],pausedAt:void 0,settings:{toastLimit:He}},B={},V=(e,t=L)=>{B[t]=Ue(B[t]||z,e),R.forEach(([e,n])=>{e===t&&n(B[t])})},H=e=>Object.keys(B).forEach(t=>V(e,t)),We=e=>Object.keys(B).find(t=>B[t].toasts.some(t=>t.id===e)),U=(e=L)=>t=>{V(t,e)},Ge={blank:4e3,error:4e3,success:2e3,loading:1/0,custom:4e3},Ke=(e={},t=L)=>{let[n,r]=(0,D.useState)(B[t]||z),i=(0,D.useRef)(B[t]);(0,D.useEffect)(()=>(i.current!==B[t]&&r(B[t]),R.push([t,r]),()=>{let e=R.findIndex(([e])=>e===t);e>-1&&R.splice(e,1)}),[t]);let a=n.toasts.map(t=>({...e,...e[t.type],...t,removeDelay:t.removeDelay||e[t.type]?.removeDelay||e?.removeDelay,duration:t.duration||e[t.type]?.duration||e?.duration||Ge[t.type],style:{...e.style,...e[t.type]?.style,...t.style}}));return{...n,toasts:a}},qe=(e,t=`blank`,n)=>({createdAt:Date.now(),visible:!0,dismissed:!1,type:t,ariaProps:{role:`status`,"aria-live":`polite`},message:e,pauseDuration:0,...n,id:n?.id||Be()}),W=e=>(t,n)=>{let r=qe(t,e,n);return U(r.toasterId||We(r.id))({type:2,toast:r}),r.id},G=(e,t)=>W(`blank`)(e,t);G.error=W(`error`),G.success=W(`success`),G.loading=W(`loading`),G.custom=W(`custom`),G.dismiss=(e,t)=>{let n={type:3,toastId:e};t?U(t)(n):H(n)},G.dismissAll=e=>G.dismiss(void 0,e),G.remove=(e,t)=>{let n={type:4,toastId:e};t?U(t)(n):H(n)},G.removeAll=e=>G.remove(void 0,e),G.promise=(e,t,n)=>{let r=G.loading(t.loading,{...n,...n?.loading});return typeof e==`function`&&(e=e()),e.then(e=>{let i=t.success?I(t.success,e):void 0;return i?G.success(i,{id:r,...n,...n?.success}):G.dismiss(r),e}).catch(e=>{let i=t.error?I(t.error,e):void 0;i?G.error(i,{id:r,...n,...n?.error}):G.dismiss(r)}),e};var Je=1e3,Ye=(e,t=`default`)=>{let{toasts:n,pausedAt:r}=Ke(e,t),i=(0,D.useRef)(new Map).current,a=(0,D.useCallback)((e,t=Je)=>{if(i.has(e))return;let n=setTimeout(()=>{i.delete(e),o({type:4,toastId:e})},t);i.set(e,n)},[]);(0,D.useEffect)(()=>{if(r)return;let e=Date.now(),i=n.map(n=>{if(n.duration===1/0)return;let r=(n.duration||0)+n.pauseDuration-(e-n.createdAt);if(r<0){n.visible&&G.dismiss(n.id);return}return setTimeout(()=>G.dismiss(n.id,t),r)});return()=>{i.forEach(e=>e&&clearTimeout(e))}},[n,r,t]);let o=(0,D.useCallback)(U(t),[t]),s=(0,D.useCallback)(()=>{o({type:5,time:Date.now()})},[o]),c=(0,D.useCallback)((e,t)=>{o({type:1,toast:{id:e,height:t}})},[o]),l=(0,D.useCallback)(()=>{r&&o({type:6,time:Date.now()})},[r,o]),u=(0,D.useCallback)((e,t)=>{let{reverseOrder:r=!1,gutter:i=8,defaultPosition:a}=t||{},o=n.filter(t=>(t.position||a)===(e.position||a)&&t.height),s=o.findIndex(t=>t.id===e.id),c=o.filter((e,t)=>t<s&&e.visible).length;return o.filter(e=>e.visible).slice(...r?[c+1]:[0,c]).reduce((e,t)=>e+(t.height||0)+i,0)},[n]);return(0,D.useEffect)(()=>{n.forEach(e=>{if(e.dismissed)a(e.id,e.removeDelay);else{let t=i.get(e.id);t&&(clearTimeout(t),i.delete(e.id))}})},[n,a]),{toasts:n,handlers:{updateHeight:c,startPause:s,endPause:l,calculateOffset:u}}},Xe=P`
from {
  transform: scale(0) rotate(45deg);
	opacity: 0;
}
to {
 transform: scale(1) rotate(45deg);
  opacity: 1;
}`,Ze=P`
from {
  transform: scale(0);
  opacity: 0;
}
to {
  transform: scale(1);
  opacity: 1;
}`,Qe=P`
from {
  transform: scale(0) rotate(90deg);
	opacity: 0;
}
to {
  transform: scale(1) rotate(90deg);
	opacity: 1;
}`,$e=F(`div`)`
  width: 20px;
  opacity: 0;
  height: 20px;
  border-radius: 10px;
  background: ${e=>e.primary||`#ff4b4b`};
  position: relative;
  transform: rotate(45deg);

  animation: ${Xe} 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)
    forwards;
  animation-delay: 100ms;

  &:after,
  &:before {
    content: '';
    animation: ${Ze} 0.15s ease-out forwards;
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
    animation: ${Qe} 0.15s ease-out forwards;
    animation-delay: 180ms;
    transform: rotate(90deg);
  }
`,et=P`
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
`,tt=F(`div`)`
  width: 12px;
  height: 12px;
  box-sizing: border-box;
  border: 2px solid;
  border-radius: 100%;
  border-color: ${e=>e.secondary||`#e0e0e0`};
  border-right-color: ${e=>e.primary||`#616161`};
  animation: ${et} 1s linear infinite;
`,nt=P`
from {
  transform: scale(0) rotate(45deg);
	opacity: 0;
}
to {
  transform: scale(1) rotate(45deg);
	opacity: 1;
}`,rt=P`
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
}`,it=F(`div`)`
  width: 20px;
  opacity: 0;
  height: 20px;
  border-radius: 10px;
  background: ${e=>e.primary||`#61d345`};
  position: relative;
  transform: rotate(45deg);

  animation: ${nt} 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)
    forwards;
  animation-delay: 100ms;
  &:after {
    content: '';
    box-sizing: border-box;
    animation: ${rt} 0.2s ease-out forwards;
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
`,at=F(`div`)`
  position: absolute;
`,ot=F(`div`)`
  position: relative;
  display: flex;
  justify-content: center;
  align-items: center;
  min-width: 20px;
  min-height: 20px;
`,st=P`
from {
  transform: scale(0.6);
  opacity: 0.4;
}
to {
  transform: scale(1);
  opacity: 1;
}`,ct=F(`div`)`
  position: relative;
  transform: scale(0.6);
  opacity: 0.4;
  min-width: 20px;
  animation: ${st} 0.3s 0.12s cubic-bezier(0.175, 0.885, 0.32, 1.275)
    forwards;
`,lt=({toast:e})=>{let{icon:t,type:n,iconTheme:r}=e;return t===void 0?n===`blank`?null:D.createElement(ot,null,D.createElement(tt,{...r}),n!==`loading`&&D.createElement(at,null,n===`error`?D.createElement($e,{...r}):D.createElement(it,{...r}))):typeof t==`string`?D.createElement(ct,null,t):t},ut=e=>`
0% {transform: translate3d(0,${e*-200}%,0) scale(.6); opacity:.5;}
100% {transform: translate3d(0,0,0) scale(1); opacity:1;}
`,dt=e=>`
0% {transform: translate3d(0,0,-1px) scale(1); opacity:1;}
100% {transform: translate3d(0,${e*-150}%,-1px) scale(.6); opacity:0;}
`,ft=`0%{opacity:0;} 100%{opacity:1;}`,pt=`0%{opacity:1;} 100%{opacity:0;}`,mt=F(`div`)`
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
`,ht=F(`div`)`
  display: flex;
  justify-content: center;
  margin: 4px 10px;
  color: inherit;
  flex: 1 1 auto;
  white-space: pre-line;
`,gt=(e,t)=>{let n=e.includes(`top`)?1:-1,[r,i]=Ve()?[ft,pt]:[ut(n),dt(n)];return{animation:t?`${P(r)} 0.35s cubic-bezier(.21,1.02,.73,1) forwards`:`${P(i)} 0.4s forwards cubic-bezier(.06,.71,.55,1)`}},_t=D.memo(({toast:e,position:t,style:n,children:r})=>{let i=e.height?gt(e.position||t||`top-center`,e.visible):{opacity:0},a=D.createElement(lt,{toast:e}),o=D.createElement(ht,{...e.ariaProps},I(e.message,e));return D.createElement(mt,{className:e.className,style:{...i,...n,...e.style}},typeof r==`function`?r({icon:a,message:o}):D.createElement(D.Fragment,null,a,o))});Re(D.createElement);var vt=({id:e,className:t,style:n,onHeightUpdate:r,children:i})=>{let a=D.useCallback(t=>{if(t){let n=()=>{let n=t.getBoundingClientRect().height;r(e,n)};n(),new MutationObserver(n).observe(t,{subtree:!0,childList:!0,characterData:!0})}},[e,r]);return D.createElement(`div`,{ref:a,className:t,style:n},i)},yt=(e,t)=>{let n=e.includes(`top`),r=n?{top:0}:{bottom:0},i=e.includes(`center`)?{justifyContent:`center`}:e.includes(`right`)?{justifyContent:`flex-end`}:{};return{left:0,right:0,display:`flex`,position:`absolute`,transition:Ve()?void 0:`all 230ms cubic-bezier(.21,1.02,.73,1)`,transform:`translateY(${t*(n?1:-1)}px)`,...r,...i}},bt=j`
  z-index: 9999;
  > * {
    pointer-events: auto;
  }
`,K=16,xt=({reverseOrder:e,position:t=`top-center`,toastOptions:n,gutter:r,children:i,toasterId:a,containerStyle:o,containerClassName:s})=>{let{toasts:c,handlers:l}=Ye(n,a);return D.createElement(`div`,{"data-rht-toaster":a||``,style:{position:`fixed`,zIndex:9999,top:K,left:K,right:K,bottom:K,pointerEvents:`none`,...o},className:s,onMouseEnter:l.startPause,onMouseLeave:l.endPause},c.map(n=>{let a=n.position||t,o=yt(a,l.calculateOffset(n,{reverseOrder:e,gutter:r,defaultPosition:t}));return D.createElement(vt,{id:n.id,key:n.id,onHeightUpdate:l.updateHeight,className:n.visible?bt:``,style:o},n.type===`custom`?I(n.message,n):i?i(n):D.createElement(_t,{toast:n,position:a}))}))},St=G,Ct=`__monitor_ui_chunk_reload_build__`;function wt(){return import.meta?.url?String(import.meta.url):typeof window<`u`?document.querySelector(`script[type="module"][src*="/monitor/assets/index-"]`)?.getAttribute(`src`)||window.location.pathname:`unknown-build`}function q(e){let t=String(e?.message||e?.reason?.message||e?.payload?.message||e||``).toLowerCase();return t.includes(`failed to fetch dynamically imported module`)||t.includes(`error loading dynamically imported module`)||t.includes(`importing a module script failed`)||t.includes(`unable to preload css`)}function J(){if(typeof window>`u`)return!1;let e=wt();try{if(sessionStorage.getItem(Ct)===e)return!1;sessionStorage.setItem(Ct,e)}catch{}return window.location.reload(),!0}function Tt(){typeof window>`u`||(window.addEventListener(`vite:preloadError`,e=>{q(e)&&(e.preventDefault(),J())}),window.addEventListener(`error`,e=>{q(e?.error||e?.message||e)&&J()}),window.addEventListener(`unhandledrejection`,e=>{q(e?.reason||e)&&J()}))}var Y=c(),Et=class extends D.Component{constructor(e){super(e),this.state={hasError:!1,error:null,errorInfo:null}}static getDerivedStateFromError(e){return{hasError:!0,error:e}}componentDidCatch(e,t){this.setState({errorInfo:t}),console.error(`[ErrorBoundary] Uncaught error:`,e,t),q(e)&&J()}handleReset=()=>{this.setState({hasError:!1,error:null,errorInfo:null})};render(){return this.state.hasError?(0,Y.jsx)(`div`,{className:`min-h-screen flex items-center justify-center bg-gray-50 p-8`,children:(0,Y.jsxs)(`div`,{className:`max-w-lg w-full text-center space-y-6`,children:[(0,Y.jsx)(`div`,{className:`flex justify-center`,children:(0,Y.jsx)(`span`,{className:`flex items-center justify-center w-16 h-16 rounded-2xl bg-red-100 text-red-500`,children:(0,Y.jsx)(ie,{size:32})})}),(0,Y.jsxs)(`div`,{children:[(0,Y.jsx)(`h1`,{className:`text-xl font-semibold text-gray-900`,children:`页面渲染出错`}),(0,Y.jsx)(`p`,{className:`mt-2 text-sm text-gray-500`,children:`控制台遇到了一个意外错误。你可以尝试重新加载当前页面。`})]}),this.state.error&&(0,Y.jsx)(`pre`,{className:`text-left rounded-lg border border-red-200 bg-red-50 p-4 text-xs text-red-700 overflow-auto max-h-40`,children:String(this.state.error)}),(0,Y.jsxs)(`div`,{className:`flex items-center justify-center gap-3`,children:[(0,Y.jsxs)(`button`,{className:`inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary-600 transition-colors`,onClick:this.handleReset,children:[(0,Y.jsx)(v,{size:14}),`重试当前页面`]}),(0,Y.jsx)(`button`,{className:`inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-gray-300 bg-white text-gray-700 text-sm font-medium hover:bg-gray-50 transition-colors`,onClick:()=>window.location.reload(),children:`刷新整个应用`})]})]})}):this.props.children}},X=de(e=>({pollingEnabled:!0,pollingInterval:30,setPollingEnabled:t=>e({pollingEnabled:t}),setPollingInterval:t=>e({pollingInterval:t}),timeRange:`1h`,setTimeRange:t=>e({timeRange:t}),selectedRequestId:null,setSelectedRequestId:t=>e({selectedRequestId:t})}));function Dt(e={}){let{windowMinutes:t=60,pollingEnabled:n=!1,pollingInterval:r=15,minIntervalSeconds:i=15}=e,[a,o]=(0,D.useState)(null),[s,c]=(0,D.useState)(null),[l,u]=(0,D.useState)(!0),[d,f]=(0,D.useState)(null),p=(0,D.useCallback)(async(e={cancelled:!1})=>{u(!0),f(null);try{let[n,r]=await Promise.allSettled([ve(),ye({window_minutes:t})]);if(e.cancelled)return;n.status===`fulfilled`?o(n.value):f(n.reason?.message||`runtime snapshot load failed`),r.status===`fulfilled`&&c(r.value)}finally{e.cancelled||u(!1)}},[t]);return(0,D.useEffect)(()=>{let e={cancelled:!1};if(p(e).catch(()=>{}),!n)return()=>{e.cancelled=!0};let t=setInterval(()=>{p(e).catch(()=>{})},Math.max(r,i)*1e3);return()=>{e.cancelled=!0,clearInterval(t)}},[p,n,r,i]),{runtimeSnapshot:a,promptStats:s,loading:l,error:d,reload:(0,D.useCallback)(async()=>{await p({cancelled:!1})},[p])}}var Ot={critical:0,warning:1,info:2};function kt({mode:e,bondStage:t,replyPosture:n,dominantMotive:r,absenceBand:i,dominantMotiveRaw:a,absenceBandRaw:o}){let s=[`当前处于${e}`],c=C(a).toLowerCase(),l=C(o).toLowerCase();return t&&t!==`—`&&s.push(`关系阶段为${t}`),n&&n!==`—`&&s.push(`回复姿态是${n}`),r&&r!==`—`&&c&&c!==`none`?s.push(`当前主导动机为${r}`):i&&i!==`—`&&l&&l!==`recent`&&s.push(`当前处于${i}`),`${s.join(`，`)}。`}function At(e){return Object.entries(e?.slot_status||{}).filter(([,e])=>e!==`live`)}function jt(e,t={}){let n=t.promptStats||null,r=[],i=e?.timestamp||Date.now();if(!e)return r;let a=e.sys_mode_status||{},o=e.self_reflection_pulse?.narrative_guardrails||{},s=e.silence_state||{},c=e.response_policy||{},l=e.runtime_summary||{},u=e.degraded_reasons||[],d=At(e);if(a.degraded||a.degraded_reason){let e=C(a.degraded_reason);r.push({reason_code:`RUNTIME_MODE_DEGRADED`,title:E(`RUNTIME_MODE_DEGRADED`),detail:e?`生命体存在态轴当前处于降级状态：${e}`:`生命体存在态轴当前处于降级状态。`,ts_ms:i,severity:`warning`,source:`runtime`,to:`/runtime-mode`})}if(u.length>0){let e=u.map(Se).filter(Boolean).join(`；`);r.push({reason_code:`RUNTIME_SNAPSHOT_DEGRADED`,title:E(`RUNTIME_SNAPSHOT_DEGRADED`),detail:T(e||`运行快照中仍有关键状态未补齐。`,180),ts_ms:i,severity:`warning`,source:`runtime`,to:`/digital-life`})}d.length>0&&r.push({reason_code:`RUNTIME_SLOT_NOT_LIVE`,title:E(`RUNTIME_SLOT_NOT_LIVE`),detail:T(`共有 ${d.length} 个关键槽位未在线：${Oe(d)}`,180),ts_ms:i,severity:`warning`,source:`runtime`,to:`/digital-life`});let f=String(o.risk_level||``).toLowerCase();if(f&&f!==`low`&&f!==`none`){let e=Array.isArray(o.risk_tags)?o.risk_tags.map(Ce).filter(Boolean):[];r.push({reason_code:`REFLECTION_GUARD_ACTIVE`,title:E(`REFLECTION_GUARD_ACTIVE`),detail:T(`当前自我反思护栏风险为${be(f)}${e.length?`，涉及 ${e.join(`、`)}`:``}。`,180),ts_ms:i,severity:f===`high`||f===`critical`?`critical`:`warning`,source:`runtime`,to:`/self-model`})}s.topic_resistance_active&&r.push({reason_code:`TOPIC_RESISTANCE_ACTIVE`,title:E(`TOPIC_RESISTANCE_ACTIVE`),detail:`当前对“${s.resistant_topic||`该话题`}”存在明显抗拒，回复会更偏谨慎。`,ts_ms:i,severity:`warning`,source:`runtime`,to:`/response-policy`}),c.should_be_withdrawn&&r.push({reason_code:`WITHDRAWN_REPLY_POLICY`,title:E(`WITHDRAWN_REPLY_POLICY`),detail:`当前策略更偏短答与收敛，回复姿态为${w(l.reply_posture?.style)}。`,ts_ms:i,severity:`info`,source:`runtime`,to:`/response-policy`}),(n?.injection_detected_count??0)>0&&r.push({reason_code:`PROMPT_INJECTION_DETECTED`,title:E(`PROMPT_INJECTION_DETECTED`),detail:`当前时间窗内检测到 ${n.injection_detected_count} 次提示注入风险。`,ts_ms:i,severity:`critical`,source:`prompt`,to:`/prompt-config`}),(n?.high_risk_count??0)>0&&r.push({reason_code:`PROMPT_HIGH_RISK_BUILD`,title:E(`PROMPT_HIGH_RISK_BUILD`),detail:`当前时间窗内存在 ${n.high_risk_count} 次高风险 Prompt 构建。`,ts_ms:i,severity:`warning`,source:`prompt`,to:`/prompt-config`});let p=e.background_runtime||{};if(p&&typeof p==`object`&&Object.keys(p).length>0){let e=String(p.scheduler_status||`UNKNOWN`).toUpperCase(),t=String(p.tick_health||`unknown`).toLowerCase(),n=!!p.is_advancing,a=p.seconds_since_inner_world_tick,o=Array.isArray(p.core_ticks_missing)?p.core_ticks_missing.filter(Boolean):[];n||r.push({reason_code:`BACKGROUND_RUNTIME_NOT_ADVANCING`,title:E(`BACKGROUND_RUNTIME_NOT_ADVANCING`),detail:T(`后台推进未达预期：tick=${t||`unknown`}，scheduler=${e}，inner_world_tick 距今 ${a??`unknown`}s。`,180),ts_ms:i,severity:t===`stalled`?`critical`:`warning`,source:`runtime`,to:`/inner-world`}),o.length>0&&r.push({reason_code:`BACKGROUND_CORE_TICK_MISSING`,title:E(`BACKGROUND_CORE_TICK_MISSING`),detail:T(`后台核心任务缺失：${o.join(`、`)}。`,180),ts_ms:i,severity:`warning`,source:`runtime`,to:`/automation`}),p.persona_user_alignment_ok===!1&&r.push({reason_code:`BACKGROUND_PERSONA_MISMATCH`,title:E(`BACKGROUND_PERSONA_MISMATCH`),detail:T(`内在世界 user_id 与当前人格 user_id 未对齐，后台推进可能写入错误用户。`,180),ts_ms:i,severity:`critical`,source:`runtime`,to:`/inner-world`})}let m=e.interoception_state||{},h=Number(m.interoceptive_index||0),g=Number(m.recovery_urgency||0);(h>=.82||g>=.78)&&r.push({reason_code:`INTEROCEPTION_OVERLOAD`,title:E(`INTEROCEPTION_OVERLOAD`),detail:T(`内感受负载升高：interoceptive_index=${h.toFixed(3)}，recovery_urgency=${g.toFixed(3)}。`,180),ts_ms:i,severity:h>=.88||g>=.85?`critical`:`warning`,source:`runtime`,to:`/overview`});let _=e.vestibular_state||{},v=Number(_.jitter_index||0),y=String(_.stability_state||``).toLowerCase();(y===`critical`||y===`unstable`||v>=.72)&&r.push({reason_code:`VESTIBULAR_INSTABILITY`,title:E(`VESTIBULAR_INSTABILITY`),detail:T(`前庭稳定性异常：state=${y||`unknown`}，jitter=${v.toFixed(3)}。`,180),ts_ms:i,severity:y===`critical`||v>=.82?`critical`:`warning`,source:`runtime`,to:`/overview`});let b=e.motor_cortex_state||{};b.last_plan_id&&b.last_dispatch_ok===!1&&r.push({reason_code:`MOTOR_DISPATCH_DEGRADED`,title:E(`MOTOR_DISPATCH_DEGRADED`),detail:T(`动作分发失败：plan=${b.last_plan_id}，reason=${b.last_dispatch_reason||`unknown`}。`,180),ts_ms:i,severity:`warning`,source:`runtime`,to:`/overview`});let x=e.homeostasis_report||{},S=String(x.status||``).toLowerCase();return(S===`warning`||S===`critical`)&&r.push({reason_code:`HOMEOSTASIS_WARNING`,title:E(`HOMEOSTASIS_WARNING`),detail:T(`生命体稳态告警：status=${S}，minute_load=${x?.minute_window?.avg_cognitive_load_index??`n/a`}，hour_stress=${x?.hour_window?.avg_stress_index??`n/a`}。`,180),ts_ms:i,severity:S===`critical`?`critical`:`warning`,source:`runtime`,to:`/overview`}),r.sort((e,t)=>{let n=(Ot[e.severity]??99)-(Ot[t.severity]??99);return n===0?Number(t.ts_ms||0)-Number(e.ts_ms||0):n})}function Mt(e){let t=e?.runtime_summary||{},n=e?.motive_state?.dominant_motive,r=e?.offline_state?.absence_band||t?.current_state?.absence_band,i=Ee(e?.sys_mode),a=we(e?.bond_state?.current_stage),o=w(t.reply_posture?.style),s=Te(n),c=be(e?.self_reflection_pulse?.narrative_guardrails?.risk_level),l=xe(r);return{mode:i,bondStage:a,replyPosture:o,dominantMotive:s,riskLevel:c,absenceBand:l,headline:kt({mode:i,bondStage:a,replyPosture:o,dominantMotive:s,absenceBand:l,dominantMotiveRaw:n,absenceBandRaw:r}),notableSignals:(t.notable_signals||[]).map(De).filter(Boolean)}}var Nt=[{title:`数字生命体`,items:[{to:`/digital-life`,icon:le,label:`总览`},{to:`/emotion`,icon:he,label:`情绪`},{to:`/bond`,icon:te,label:`关系`},{to:`/memory`,icon:me,label:`记忆`},{to:`/runtime-mode`,icon:le,label:`存在态`},{to:`/inner-world`,icon:S,label:`内在世界`},{to:`/response-policy`,icon:fe,label:`回复策略`},{to:`/self-model`,icon:y,label:`自我模型`}]},{title:`运营`,items:[{to:`/prompt-config`,icon:re,label:`提示词`},{to:`/persona-config`,icon:se,label:`人格文案`},{to:`/system`,icon:b,label:`系统底座`},{to:`/canon`,icon:ee,label:`设定管理`}]},{title:`器官级观测`,items:[{to:`/overview`,icon:ne,label:`生命体征`},{to:`/sessions`,icon:m,label:`神经会话`},{to:`/trace`,icon:ce,label:`神经链路`},{to:`/metrics`,icon:g,label:`代谢指标`},{to:`/insights`,icon:d,label:`皮层洞察`},{to:`/chat-debug`,icon:_,label:`神经调试`},{to:`/alerts`,icon:x,label:`免疫告警`},{to:`/logs`,icon:oe,label:`生理日志`}]},{title:`外周执行`,items:[{to:`/gateway`,icon:p,label:`感官网关`},{to:`/skills`,icon:ge,label:`行为技能`},{to:`/tools`,icon:ae,label:`执行工具`},{to:`/automation`,icon:pe,label:`节律调度`},{to:`/agent`,icon:_e,label:`反射回路`}]}],Pt=[15,30,60],Ft={critical:`border-red-200 bg-red-50 text-red-700`,warning:`border-orange-200 bg-orange-50 text-orange-700`,info:`border-blue-200 bg-blue-50 text-blue-700`};function Z({value:e,severity:t=`info`}){return e?(0,Y.jsx)(`span`,{className:`inline-flex min-w-5 items-center justify-center rounded-full border px-1.5 py-0.5 text-[11px] font-semibold ${Ft[t]||Ft.info}`,children:e}):null}function It({to:e,icon:t,label:n,onClick:r,badge:i=null}){return(0,Y.jsxs)(s,{to:e,onClick:r,className:({isActive:e})=>`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${e?`bg-primary-50 text-primary`:`text-gray-600 hover:bg-gray-100 hover:text-gray-800`}`,children:[(0,Y.jsx)(t,{size:15}),n,i?(0,Y.jsx)(Z,{value:i.value,severity:i.severity}):null]})}function Lt(){let e=X(e=>e.pollingEnabled),t=X(e=>e.pollingInterval),n=X(e=>e.setPollingEnabled),r=X(e=>e.setPollingInterval),[i,o]=(0,D.useState)(!1),s=u(),{runtimeSnapshot:c,promptStats:l}=Dt({pollingEnabled:e,pollingInterval:t,windowMinutes:60}),d=jt(c,{promptStats:l});(0,D.useEffect)(()=>{o(!1)},[s.pathname]);let p=d.length>0?{value:d.length,severity:d[0]?.severity||`info`}:null,m={数字生命体:p},g={"/alerts":p};return(0,Y.jsxs)(`div`,{className:`relative flex h-full flex-col overflow-hidden bg-gray-50`,children:[(0,Y.jsxs)(`div`,{className:`pointer-events-none absolute inset-0 overflow-hidden`,children:[(0,Y.jsx)(`div`,{className:`absolute -left-32 top-24 h-72 w-72 rounded-full bg-primary-100/40 blur-3xl animate-pulse`}),(0,Y.jsx)(`div`,{className:`absolute right-0 top-1/3 h-96 w-96 rounded-full bg-pink-100/35 blur-3xl animate-pulse`}),(0,Y.jsx)(`div`,{className:`absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(236,72,153,0.04),_transparent_28%)]`})]}),(0,Y.jsx)(`header`,{className:`sticky top-0 z-50 border-b border-gray-200 bg-white/95 shadow-sm backdrop-blur`,children:(0,Y.jsxs)(`div`,{className:`mx-auto w-full space-y-3 px-4 py-3 sm:px-6`,children:[(0,Y.jsxs)(`div`,{className:`flex items-center justify-between gap-4`,children:[(0,Y.jsxs)(`div`,{className:`flex items-center gap-2`,children:[(0,Y.jsx)(`button`,{className:`rounded p-1.5 text-gray-500 transition-colors hover:bg-gray-100 lg:hidden`,onClick:()=>o(e=>!e),"aria-label":`切换导航菜单`,children:i?(0,Y.jsx)(ue,{size:20}):(0,Y.jsx)(h,{size:20})}),(0,Y.jsxs)(`div`,{children:[(0,Y.jsx)(`div`,{className:`text-lg font-bold tracking-tight text-primary`,children:`Natsume AI`}),(0,Y.jsx)(`div`,{className:`hidden text-xs text-gray-400 sm:block`,children:`数字生命体控制台`})]})]}),(0,Y.jsxs)(`div`,{className:`flex items-center gap-2 text-sm`,children:[(0,Y.jsxs)(`button`,{className:`flex items-center gap-1.5 rounded border px-2.5 py-1.5 text-xs font-medium transition-colors ${e?`border-green-300 bg-green-50 text-green-700 hover:bg-green-100`:`border-gray-300 bg-gray-50 text-gray-500 hover:bg-gray-100`}`,onClick:()=>n(!e),title:e?`点击暂停自动刷新`:`点击开启自动刷新`,children:[e?(0,Y.jsx)(v,{size:12,className:`animate-spin`}):(0,Y.jsx)(f,{size:12}),e?`轮询中`:`已暂停`]}),(0,Y.jsx)(`select`,{className:`rounded border border-gray-300 px-1.5 py-1.5 text-xs text-gray-600 focus:outline-none focus:ring-1 focus:ring-primary-200`,value:t,onChange:e=>r(Number(e.target.value)),title:`自动刷新间隔`,children:Pt.map(e=>(0,Y.jsxs)(`option`,{value:e,children:[e,`s`]},e))})]})]}),(0,Y.jsx)(`nav`,{className:`hidden flex-wrap gap-3 lg:flex`,role:`navigation`,"aria-label":`主导航`,children:Nt.map(e=>(0,Y.jsxs)(`div`,{className:`rounded-xl border border-gray-200 bg-gray-50 px-2.5 py-2`,children:[(0,Y.jsxs)(`div`,{className:`flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400`,children:[(0,Y.jsx)(`span`,{children:e.title}),m[e.title]?(0,Y.jsx)(Z,{value:m[e.title].value,severity:m[e.title].severity}):null]}),(0,Y.jsx)(`div`,{className:`mt-1 flex flex-wrap gap-1`,children:e.items.map(e=>(0,Y.jsx)(It,{...e,badge:g[e.to]},e.to))})]},e.title))})]})}),i?(0,Y.jsxs)(Y.Fragment,{children:[(0,Y.jsx)(`div`,{className:`fixed inset-0 z-40 bg-black/30 lg:hidden`,onClick:()=>o(!1)}),(0,Y.jsx)(`nav`,{className:`fixed bottom-0 left-0 top-[72px] z-50 w-72 overflow-y-auto border-r border-gray-200 bg-white shadow-xl lg:hidden`,role:`navigation`,"aria-label":`移动端导航`,children:(0,Y.jsx)(`div`,{className:`space-y-3 p-3`,children:Nt.map(e=>(0,Y.jsxs)(`div`,{className:`rounded-xl border border-gray-200 bg-gray-50 p-2`,children:[(0,Y.jsxs)(`div`,{className:`flex items-center gap-1.5 px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400`,children:[(0,Y.jsx)(`span`,{children:e.title}),m[e.title]?(0,Y.jsx)(Z,{value:m[e.title].value,severity:m[e.title].severity}):null]}),(0,Y.jsx)(`div`,{className:`space-y-1`,children:e.items.map(e=>(0,Y.jsx)(It,{...e,badge:g[e.to],onClick:()=>o(!1)},e.to))})]},e.title))})})]}):null,(0,Y.jsx)(`main`,{className:`relative z-10 mx-auto flex w-full min-w-0 flex-1 flex-col px-4 py-6 sm:px-6 lg:px-8`,children:(0,Y.jsx)(a,{})})]})}var Rt=(function(){let e=typeof document<`u`&&document.createElement(`link`).relList;return e&&e.supports&&e.supports(`modulepreload`)?`modulepreload`:`preload`})(),zt=function(e){return`/monitor/`+e},Bt={},Q=function(e,t,n){let r=Promise.resolve();if(t&&t.length>0){let e=document.getElementsByTagName(`link`),i=document.querySelector(`meta[property=csp-nonce]`),a=i?.nonce||i?.getAttribute(`nonce`);function o(e){return Promise.all(e.map(e=>Promise.resolve(e).then(e=>({status:`fulfilled`,value:e}),e=>({status:`rejected`,reason:e}))))}r=o(t.map(t=>{if(t=zt(t,n),t in Bt)return;Bt[t]=!0;let r=t.endsWith(`.css`),i=r?`[rel="stylesheet"]`:``;if(n)for(let n=e.length-1;n>=0;n--){let i=e[n];if(i.href===t&&(!r||i.rel===`stylesheet`))return}else if(document.querySelector(`link[href="${t}"]${i}`))return;let o=document.createElement(`link`);if(o.rel=r?`stylesheet`:Rt,r||(o.as=`script`),o.crossOrigin=``,o.href=t,a&&o.setAttribute(`nonce`,a),document.head.appendChild(o),r)return new Promise((e,n)=>{o.addEventListener(`load`,e),o.addEventListener(`error`,()=>n(Error(`Unable to preload CSS for ${t}`)))})}))}function i(e){let t=new Event(`vite:preloadError`,{cancelable:!0});if(t.payload=e,window.dispatchEvent(t),!t.defaultPrevented)throw e}return r.then(t=>{for(let e of t||[])e.status===`rejected`&&i(e.reason);return e().catch(i)})},Vt=(0,D.lazy)(()=>Q(()=>import(`./DigitalLifeOverview-xoMZmQ3H.js`),__vite__mapDeps([0]))),Ht=(0,D.lazy)(()=>Q(()=>import(`./Overview-DnrfG5Qf.js`),__vite__mapDeps([0]))),Ut=(0,D.lazy)(()=>Q(()=>import(`./Sessions-9_n_SruQ.js`),__vite__mapDeps([0]))),Wt=(0,D.lazy)(()=>Q(()=>import(`./Trace-BYGdg-6n.js`),__vite__mapDeps([0]))),Gt=(0,D.lazy)(()=>Q(()=>import(`./Metrics-DLmkx8YV.js`),__vite__mapDeps([0]))),Kt=(0,D.lazy)(()=>Q(()=>import(`./Insights-BltVBmTv.js`),__vite__mapDeps([0]))),qt=(0,D.lazy)(()=>Q(()=>import(`./Canon-7gg9XH6p.js`),__vite__mapDeps([0]))),Jt=(0,D.lazy)(()=>Q(()=>import(`./Bond-BsqMzHSw.js`),__vite__mapDeps([0]))),Yt=(0,D.lazy)(()=>Q(()=>import(`./Tools-dQ23stUA.js`),__vite__mapDeps([0]))),Xt=(0,D.lazy)(()=>Q(()=>import(`./Automation-BRdZkzy1.js`),__vite__mapDeps([0]))),Zt=(0,D.lazy)(()=>Q(()=>import(`./AgentLoop-DyCvcOw_.js`),__vite__mapDeps([0]))),Qt=(0,D.lazy)(()=>Q(()=>import(`./Gateway-Duqta05Z.js`),__vite__mapDeps([0]))),$t=(0,D.lazy)(()=>Q(()=>import(`./SkillsHub-3C-l1rkK.js`),__vite__mapDeps([0]))),en=(0,D.lazy)(()=>Q(()=>import(`./ChatDebug-Bv3JZGE8.js`),__vite__mapDeps([0]))),tn=(0,D.lazy)(()=>Q(()=>import(`./Alerts-zVoWF54a.js`),__vite__mapDeps([0]))),nn=(0,D.lazy)(()=>Q(()=>import(`./LogStream-B2i_wf57.js`),__vite__mapDeps([0]))),rn=(0,D.lazy)(()=>Q(()=>import(`./EmotionDashboard-kdp5_P4l.js`),__vite__mapDeps([0]))),an=(0,D.lazy)(()=>Q(()=>import(`./MemoryBrowser-DJZwpjDo.js`),__vite__mapDeps([0]))),on=(0,D.lazy)(()=>Q(()=>import(`./SystemConfig-DBP9b3d9.js`),__vite__mapDeps([0]))),sn=(0,D.lazy)(()=>Q(()=>import(`./RuntimeMode-CdCHPL2a.js`),__vite__mapDeps([0]))),cn=(0,D.lazy)(()=>Q(()=>import(`./PromptConfig-C6Bzn_Jp.js`),__vite__mapDeps([0]))),ln=(0,D.lazy)(()=>Q(()=>import(`./PersonaPromptOps-7dP-4SqD.js`),__vite__mapDeps([0]))),un=(0,D.lazy)(()=>Q(()=>import(`./InnerWorldDashboard-DxYosxqB.js`),__vite__mapDeps([0]))),dn=(0,D.lazy)(()=>Q(()=>import(`./ResponsePolicyDashboard-Czicw0wW.js`),__vite__mapDeps([0]))),fn=(0,D.lazy)(()=>Q(()=>import(`./SelfModelDashboard-x1g3AcH5.js`),__vite__mapDeps([0])));function pn(){return(0,Y.jsx)(`div`,{className:`rounded-2xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500 shadow-sm`,children:`正在加载页面…`})}function $(e){return(0,Y.jsx)(D.Suspense,{fallback:(0,Y.jsx)(pn,{}),children:e})}function mn(){return(0,Y.jsx)(Et,{children:(0,Y.jsxs)(i,{children:[(0,Y.jsx)(l,{children:(0,Y.jsxs)(r,{path:`/`,element:(0,Y.jsx)(Lt,{}),children:[(0,Y.jsx)(r,{index:!0,element:(0,Y.jsx)(o,{to:`/digital-life`,replace:!0})}),(0,Y.jsx)(r,{path:`digital-life`,element:$((0,Y.jsx)(Vt,{}))}),(0,Y.jsx)(r,{path:`overview`,element:$((0,Y.jsx)(Ht,{}))}),(0,Y.jsx)(r,{path:`sessions`,element:$((0,Y.jsx)(Ut,{}))}),(0,Y.jsx)(r,{path:`trace/*`,element:$((0,Y.jsx)(Wt,{}))}),(0,Y.jsx)(r,{path:`metrics`,element:$((0,Y.jsx)(Gt,{}))}),(0,Y.jsx)(r,{path:`insights/*`,element:$((0,Y.jsx)(Kt,{}))}),(0,Y.jsx)(r,{path:`chat-debug`,element:$((0,Y.jsx)(en,{}))}),(0,Y.jsx)(r,{path:`alerts`,element:$((0,Y.jsx)(tn,{}))}),(0,Y.jsx)(r,{path:`logs`,element:$((0,Y.jsx)(nn,{}))}),(0,Y.jsx)(r,{path:`emotion`,element:$((0,Y.jsx)(rn,{}))}),(0,Y.jsx)(r,{path:`memory`,element:$((0,Y.jsx)(an,{}))}),(0,Y.jsx)(r,{path:`system`,element:$((0,Y.jsx)(on,{}))}),(0,Y.jsx)(r,{path:`prompt-config`,element:$((0,Y.jsx)(cn,{}))}),(0,Y.jsx)(r,{path:`persona-config`,element:$((0,Y.jsx)(ln,{}))}),(0,Y.jsx)(r,{path:`runtime-mode`,element:$((0,Y.jsx)(sn,{}))}),(0,Y.jsx)(r,{path:`inner-world`,element:$((0,Y.jsx)(un,{}))}),(0,Y.jsx)(r,{path:`response-policy`,element:$((0,Y.jsx)(dn,{}))}),(0,Y.jsx)(r,{path:`self-model`,element:$((0,Y.jsx)(fn,{}))}),(0,Y.jsx)(r,{path:`canon`,element:$((0,Y.jsx)(qt,{}))}),(0,Y.jsx)(r,{path:`bond`,element:$((0,Y.jsx)(Jt,{}))}),(0,Y.jsx)(r,{path:`tools`,element:$((0,Y.jsx)(Yt,{}))}),(0,Y.jsx)(r,{path:`automation`,element:$((0,Y.jsx)(Xt,{}))}),(0,Y.jsx)(r,{path:`agent`,element:$((0,Y.jsx)(Zt,{}))}),(0,Y.jsx)(r,{path:`gateway`,element:$((0,Y.jsx)(Qt,{}))}),(0,Y.jsx)(r,{path:`skills`,element:$((0,Y.jsx)($t,{}))}),(0,Y.jsx)(r,{path:`wechat`,element:(0,Y.jsx)(o,{to:`/gateway`,replace:!0})})]})}),(0,Y.jsx)(xt,{position:`top-right`,toastOptions:{duration:4e3,style:{fontSize:`13px`}}})]})})}Tt(),ke.createRoot(document.getElementById(`root`)).render((0,Y.jsx)(D.StrictMode,{children:(0,Y.jsx)(mn,{})}));export{Dt as a,At as i,jt as n,X as o,Mt as r,St as s,Q as t};
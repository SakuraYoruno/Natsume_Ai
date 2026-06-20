function e(e){let t=String(e||``).split(`
`).filter(e=>e.startsWith(`data:`)).map(e=>e.slice(5).trim()).join(`
`);if(!t)return null;try{return JSON.parse(t)}catch{return{raw:t}}}function t(t){let n=String(t||``).split(`

`),r=n.pop()??``;return{events:n.map(e).filter(Boolean),remainder:r}}export{t};
'use strict';
const categoryNames=Object.fromEntries(CATALOG.categories.flatMap(c=>[[c.slug,c.name],...c.children.map(x=>[x.slug,x.name])]));
const CategoryNet=(()=>{
 const labels=[...new Set(CATALOG.suppliers.flatMap(s=>s.categories))], D=128,H=16,K=labels.length;
 let seed=19;const rand=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296-.5;};
 const W=Array.from({length:H},()=>Float64Array.from({length:D},()=>rand()*.15));
 const V=Array.from({length:K},()=>Float64Array.from({length:H},()=>rand()*.15));
 function vector(text){const x=new Float64Array(D);for(const word of text.toLowerCase().match(/[а-яёa-z0-9]+/g)||[]){for(let j=0;j<word.length-1;j++){let h=0;for(const c of word.slice(j,j+3))h=(Math.imul(h,31)+c.charCodeAt(0))>>>0;x[h%D]++;}}const n=Math.hypot(...x)||1;return x.map(v=>v/n);}
 function forward(x){const h=W.map(w=>Math.tanh(w.reduce((a,b,i)=>a+b*x[i],0)));let z=V.map(w=>w.reduce((a,b,i)=>a+b*h[i],0));const m=Math.max(...z);z=z.map(v=>Math.exp(v-m));const sum=z.reduce((a,b)=>a+b,0);return {h,p:z.map(v=>v/sum)};}
 const rows=CATALOG.suppliers.map(s=>({x:vector(s.name+' '+s.description+' '+s.categories.map(c=>categoryNames[c]||'').join(' ')),ys:s.categories.map(c=>labels.indexOf(c))}));
 for(let e=0;e<70;e++)for(const r of rows){const {h,p}=forward(r.x);const delta=p.map((v,k)=>v-(r.ys.includes(k)?1/r.ys.length:0));const dh=h.map((v,j)=>delta.reduce((a,d,k)=>a+d*V[k][j],0)*(1-v*v));for(let k=0;k<K;k++)for(let j=0;j<H;j++)V[k][j]-=.12*delta[k]*h[j];for(let j=0;j<H;j++)for(let i=0;i<D;i++)W[j][i]-=.12*dh[j]*r.x[i];}
 return {scores(text){const {p}=forward(vector(text));return Object.fromEntries(labels.map((v,i)=>[v,p[i]]));}};
})();

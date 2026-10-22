(function(root){
  'use strict';
  function hex(value){return typeof value==='string'&&/^#[\da-f]{6}$/i.test(value)?value.toLowerCase():null;}
  function rgb(value){const color=hex(value);if(!color)throw new Error('Введите цвет в формате #RRGGBB.');return [1,3,5].map(i=>parseInt(color.slice(i,i+2),16)/255);}
  function luminance(value){const values=rgb(value).map(v=>v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4);return .2126*values[0]+.7152*values[1]+.0722*values[2];}
  function contrast(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
  function onAccent(value){return contrast(value,'#ffffff')>=contrast(value,'#08111d')?'#ffffff':'#08111d';}
  function validate(palette){
    if(!['customAccent','customBg','customSurface','customText'].every(key=>hex(palette[key])))return 'Для каждого цвета нужен код #RRGGBB.';
    if(contrast(palette.customText,palette.customBg)<4.5||contrast(palette.customText,palette.customSurface)<4.5)return 'Текст сливается с фоном или карточками. Измените цвет текста либо сделайте фон и карточки ближе по светлоте.';
    if(contrast(palette.customAccent,palette.customBg)<4.5||contrast(palette.customAccent,palette.customSurface)<4.5)return 'Акцент сливается с фоном или карточками. Выберите более контрастный акцент.';
    return '';
  }
  const api={hex,rgb,luminance,contrast,onAccent,validate};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.SupplyTheme=api;
})(typeof window!=='undefined'?window:globalThis);

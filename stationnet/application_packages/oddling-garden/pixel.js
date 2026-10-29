export function createPixelRenderer(ui, W, H, PIXEL_SIZE) {
  const PW = W / PIXEL_SIZE, PH = H / PIXEL_SIZE;
  const pixelFrame = ui.graphics.framebuffer("oddling-pixels", PW, PH, { format: "rgba" });
  const pixelColors = new Map();
  function rgba(color) {
    if (pixelColors.has(color)) return pixelColors.get(color);
    const hex = (color || "#00ed45").replace("#", "");
    let bytes;
    if (hex.length === 6 || hex.length === 8) {
      bytes = [parseInt(hex.slice(0,2),16), parseInt(hex.slice(2,4),16),
        parseInt(hex.slice(4,6),16),255];
    } else if (hex.length === 3 || hex.length === 4) {
      bytes = [0,1,2].map(i => parseInt(hex[i] + hex[i],16));
      bytes.push(255);
    } else bytes = [0,237,69,255];
    pixelColors.set(color,bytes);
    return bytes;
  }
  function putPixel(x,y,c) {
    if (x<0 || y<0 || x>=PW || y>=PH) return;
    const index=(y*PW+x)*4, px=pixelFrame.pixels;
    px[index]=c[0]; px[index+1]=c[1]; px[index+2]=c[2]; px[index+3]=255;
  }
  function pixelRect(x,y,w,h,color) {
    if(w<=0 || h<=0) return;
    const c=rgba(color);
    const l=Math.max(0,Math.floor(x/PIXEL_SIZE)), r=Math.min(PW,Math.ceil((x+w)/PIXEL_SIZE));
    const top=Math.max(0,Math.floor(y/PIXEL_SIZE)), bot=Math.min(PH,Math.ceil((y+h)/PIXEL_SIZE));
    for(let py=top;py<bot;py++) for(let px=l;px<r;px++) putPixel(px,py,c);
  }
  function pixelCircle(x,y,r,color) {
    if(r<=0) return;
    const c=rgba(color), rr=r*r;
    const l=Math.max(0,Math.floor((x-r)/PIXEL_SIZE)), rt=Math.min(PW,Math.ceil((x+r)/PIXEL_SIZE));
    const top=Math.max(0,Math.floor((y-r)/PIXEL_SIZE)), bot=Math.min(PH,Math.ceil((y+r)/PIXEL_SIZE));
    for(let py=top;py<bot;py++) for(let px=l;px<rt;px++) {
      const dx=(px+.5)*PIXEL_SIZE-x, dy=(py+.5)*PIXEL_SIZE-y;
      if(dx*dx+dy*dy<=rr) putPixel(px,py,c);
    }
  }
  function pixelLine(x1,y1,x2,y2,width,color) {
    const c=rgba(color), r=Math.max(1,width/2), rr=r*r;
    const dx=x2-x1,dy=y2-y1,len=dx*dx+dy*dy;
    const l=Math.max(0,Math.floor((Math.min(x1,x2)-r)/PIXEL_SIZE));
    const rt=Math.min(PW,Math.ceil((Math.max(x1,x2)+r)/PIXEL_SIZE));
    const top=Math.max(0,Math.floor((Math.min(y1,y2)-r)/PIXEL_SIZE));
    const bot=Math.min(PH,Math.ceil((Math.max(y1,y2)+r)/PIXEL_SIZE));
    for(let py=top;py<bot;py++) for(let px=l;px<rt;px++) {
      const x=(px+.5)*PIXEL_SIZE,y=(py+.5)*PIXEL_SIZE;
      const u=len ? Math.max(0,Math.min(1,((x-x1)*dx+(y-y1)*dy)/len)) : 0;
      const ex=x-(x1+dx*u),ey=y-(y1+dy*u);
      if(ex*ex+ey*ey<=rr) putPixel(px,py,c);
    }
  }
  function pixelPolygon(points,fill,stroke,width) {
    if (!Array.isArray(points) || points.length<3) return;
    if (fill) {
      const color=rgba(fill), xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
      const l=Math.max(0,Math.floor(Math.min(...xs)/PIXEL_SIZE));
      const rt=Math.min(PW,Math.ceil(Math.max(...xs)/PIXEL_SIZE));
      const top=Math.max(0,Math.floor(Math.min(...ys)/PIXEL_SIZE));
      const bot=Math.min(PH,Math.ceil(Math.max(...ys)/PIXEL_SIZE));
      for(let py=top;py<bot;py++) for(let px=l;px<rt;px++) {
        const x=(px+.5)*PIXEL_SIZE,y=(py+.5)*PIXEL_SIZE;
        let inside=false;
        for(let i=0,j=points.length-1;i<points.length;j=i++) {
          const a=points[i],b=points[j];
          if((a[1]>y)!==(b[1]>y) && x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]) inside=!inside;
        }
        if(inside) putPixel(px,py,color);
      }
    }
    if (stroke) for(let i=0;i<points.length;i++) {
      const a=points[i],b=points[(i+1)%points.length];
      pixelLine(a[0],a[1],b[0],b[1],width||2,stroke);
    }
  }
  function pixelScene(commands) {
    const output=[];
    const words=[];
    pixelFrame.clear([0,0,0,255]);
    for(const c of commands) {
      if(c.type==="rect") pixelRect(c.x,c.y,c.width,c.height,c.color);
      else if(c.type==="circle") pixelCircle(c.x,c.y,c.radius,c.color);
      else if(c.type==="line") pixelLine(c.x1,c.y1,c.x2,c.y2,c.width,c.color);
      else if(c.type==="polygon") pixelPolygon(c.points,c.fill,c.stroke,c.width);
      else if(c.type==="text") words.push(c);
    }
    pixelFrame.present();
    output.push(ui.graphics.frame(pixelFrame,0,0,W,H));
    for(const c of words) {
      const font=c.size>=15 ? ui.graphics.fonts.vga8x16 : ui.graphics.fonts.vga8x8;
      const text=String(c.text).slice(0,45);
      const width=8*text.length;
      const x=Math.round(c.x-(c.align==="center" ? width/2 : c.align==="right" ? width : 0));
      const y=Math.round(c.y-(c.size>=15 ? 8 : 4));
      output.push(ui.graphics.bitmapText(text,font,x,y,{color:c.color}));
    }
    return output;
  }
  return pixelScene;
}

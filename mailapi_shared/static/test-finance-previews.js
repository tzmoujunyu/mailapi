/* Invoice thumbnails reuse the project's PhotoSwipe viewer inside the detail dialog. */
window.FinancePreviews = (() => {
  const dialog=document.getElementById('fin-detail-dialog'),t=value=>window.SiteLanguage?.t(value)||value;
  let abort=null,sequence=0,gallery=null,library=null;
  function reset(){sequence++;abort?.abort();abort=null;gallery?.destroy();gallery=null;}
  function loadViewer(){
    return library ||= window.PagePerformance.loadScript('/static/vendor/photoswipe/photoswipe.js')
      .then(()=>window.PhotoSwipe).catch(error=>{library=null;throw error;});
  }
  async function render(item,event){
    reset();const host=document.getElementById('fin-detail-previews');
    if(!host||!item?.files?.some(file=>file.kind==='invoice'))return;
    const request=new AbortController(),seq=sequence;abort=request;
    const status=document.createElement('p');status.className='fin-secondary';status.setAttribute('role','status');
    status.textContent=t('正在加载发票预览…');host.append(status);
    try{
      const response=await fetch(`/api/finance/applications/${encodeURIComponent(item.id)}/invoice-preview${event?'?event_id='+encodeURIComponent(event.id):''}`,{cache:'no-store',signal:request.signal});
      const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?t(data.detail):t('发票预览加载失败，请下载原文件查看'));
      if(request.signal.aborted||seq!==sequence||!dialog.open)return;
      const heading=document.createElement('h4');heading.textContent=t('发票预览');host.prepend(heading);
      const grid=document.createElement('div');grid.className='fin-invoice-thumbnails';host.append(grid);
      data.pages.forEach((page,index)=>{
        const button=document.createElement('button');button.type='button';button.className='fin-invoice-thumbnail';
        button.setAttribute('aria-label',`${t('放大发票')} · ${index+1} / ${data.pages.length}`);
        const image=document.createElement('img');image.src=page.thumbnail;image.alt=`${t('发票缩略图')} · ${index+1}`;image.loading='lazy';
        image.addEventListener('error',()=>{if(seq===sequence)status.textContent=t('发票预览加载失败，请下载原文件查看');});
        const caption=document.createElement('span');caption.textContent=`${t('点击放大')} · ${index+1} / ${data.pages.length}`;
        button.append(image,caption);grid.append(button);
        button.addEventListener('click',async()=>{
          try{
            const Viewer=await loadViewer();if(seq!==sequence||!dialog.open)return;
            const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            const shown=new Viewer({dataSource:data.pages.map(page=>({...page,alt:t('发票大图')})),index,appendToEl:dialog,bgOpacity:.95,
              returnFocus:true,showAnimationDuration:reduced?0:160,hideAnimationDuration:reduced?0:160,
              closeTitle:t('关闭大图'),zoomTitle:t('放大或缩小'),arrowPrevTitle:t('上一页'),arrowNextTitle:t('下一页'),
              errorMsg:t('发票预览加载失败，请下载原文件查看')});
            gallery=shown;shown.on('destroy',()=>{if(gallery===shown)gallery=null;if(button.isConnected&&dialog.open)button.focus();});shown.init();
          }catch{if(seq===sequence)status.textContent=t('大图加载失败，请下载原文件查看');}
        });
      });
      status.textContent=t(data.pages.length>1?'点击缩略图查看大图，可放大和翻页':'点击缩略图查看大图，可放大查看');
    }catch(error){if(!request.signal.aborted&&seq===sequence)status.textContent=error.message;}
  }
  dialog.addEventListener('cancel',event=>{if(gallery){event.preventDefault();gallery.close();}});
  dialog.addEventListener('close',reset);
  return {render,reset};
})();

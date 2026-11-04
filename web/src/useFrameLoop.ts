import { useEffect, useRef, type RefObject } from "react";

/**
 * 视频播放时每一帧调一次 ``onFrame``（暂停时不调）。
 *
 * 浏览器的 timeupdate 事件一秒只有 4 次左右，用它判断「该跳过剪掉的部分了」会多播出
 * 最多 0.25 秒被剪掉的内容；按帧检查才能剪得干净。支持 requestVideoFrameCallback 的
 * 浏览器按视频帧回调，其他的退回 requestAnimationFrame。
 */
export function useFrameLoop(video: RefObject<HTMLVideoElement>, onFrame: () => void, active = true): void {
  // 回调放在 ref 里：每次渲染换新函数也不用重新挂监听。
  const callback = useRef(onFrame);
  callback.current = onFrame;
  useEffect(() => {
    const el = video.current;
    if (!el || !active) return;
    type FrameVideo = HTMLVideoElement & {
      requestVideoFrameCallback?: (cb: () => void) => number;
      cancelVideoFrameCallback?: (handle: number) => void;
    };
    const frameVideo = el as FrameVideo;
    let handle = 0;
    let usingVideoFrame = false;
    let running = false;

    const tick = () => {
      if (!running) return;
      callback.current();
      schedule();
    };
    const schedule = () => {
      if (!running) return;
      if (frameVideo.requestVideoFrameCallback) {
        usingVideoFrame = true;
        handle = frameVideo.requestVideoFrameCallback(tick);
      } else {
        handle = window.requestAnimationFrame(tick);
      }
    };
    const cancel = () => {
      if (!handle) return;
      if (usingVideoFrame) frameVideo.cancelVideoFrameCallback?.(handle);
      else window.cancelAnimationFrame(handle);
      handle = 0;
    };
    const start = () => {
      if (running) return;
      running = true;
      schedule();
    };
    const stop = () => {
      running = false;
      cancel();
    };

    el.addEventListener("play", start);
    el.addEventListener("pause", stop);
    el.addEventListener("ended", stop);
    if (!el.paused) start();
    return () => {
      stop();
      el.removeEventListener("play", start);
      el.removeEventListener("pause", stop);
      el.removeEventListener("ended", stop);
    };
  }, [video, active]);
}

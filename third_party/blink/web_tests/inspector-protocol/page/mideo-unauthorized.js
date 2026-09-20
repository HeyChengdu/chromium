(async function(testRunner) {
  const {dp} = await testRunner.startBlank('共享帧接口必须显式授权，未知槽不能释放。');
  const capture = await dp.Page.captureScreenshot({format: 'mideo-shm'});
  testRunner.log('未授权捕获被拒绝: ' + Boolean(capture.error));
  const release = await dp.Page.releaseMideoFrame({slot: 0, sequence: '0'});
  testRunner.log('未知槽被拒绝: ' + Boolean(release.error));
  testRunner.completeTest();
})

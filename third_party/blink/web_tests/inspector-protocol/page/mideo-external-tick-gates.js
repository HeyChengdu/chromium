// [INPUT]: Inspector Protocol harness 的真实 Page 会话。
// [OUTPUT]: 实验参数类型及本地共享会话权限门禁结果。
// [POS]: Page CDP 回归；不替代 Headless mmap 像素合同。
// [PROTOCOL]: 变更时检查 AGENTS.md。
(async function(testRunner) {
  const {dp} = await testRunner.startBlank('Mideo external tick protocol gates');
  const malformed = await dp.Page.captureMideoFrame({prepareForExternalTick: 'true'});
  testRunner.log(`Rejects non-boolean experiment parameter: ${malformed.error?.code === -32602}`);
  const untrusted = await dp.Page.captureMideoFrame({prepareForExternalTick: true});
  testRunner.log(`Preserves trusted local shared-frame gate: ${
      untrusted.error?.message === 'Mideo requires a trusted local shared-frame session'}`);
  testRunner.completeTest();
})

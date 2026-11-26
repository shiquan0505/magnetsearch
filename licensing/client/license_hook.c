/* 编进 libMagnetSearchPatch.dylib 的钩子：
 * 既保证 dyld 把 libMagnetSearchLicense.dylib 作为依赖加载，
 * 也保证补丁库加载完就立刻启动授权门禁。 */
extern void ms_license_boot(void);

__attribute__((constructor))
static void ms_license_hook(void) {
    ms_license_boot();
}

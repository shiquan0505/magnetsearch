/* 本库被直接注入（LC_LOAD_DYLIB）时的自举入口。
 * 同时 libMagnetSearchPatch.dylib 也会调用 ms_license_boot()，boot() 内部幂等。 */
extern void ms_license_boot(void);

__attribute__((constructor))
static void ms_license_ctor(void) {
    ms_license_boot();
}

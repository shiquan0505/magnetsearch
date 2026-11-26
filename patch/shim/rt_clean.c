/* macOS 13 (Swift 5.7) 缺失的 Swift 6 运行时入口和 SwiftUI(macOS 14+) 符号转发层 */
#include <stddef.h>

/* ---- 已存在于 macOS 12/13 的旧入口 ---- */
extern const void *swift_getTypeByMangledNameInContext(const char *, size_t, const void *, const void *const *) __attribute__((swiftcall));
extern const void *swift_getTypeByMangledNameInContextInMetadataState(size_t, const char *, size_t, const void *, const void *const *) __attribute__((swiftcall));
extern const void *swift_getOpaqueTypeConformance(const void * const *, const void *, unsigned) __attribute__((swiftcall));

__attribute__((swiftcall, visibility("default")))
const void *swift_getTypeByMangledNameInContext2(const char *n, size_t l, const void *c, const void *const *a) {
    return swift_getTypeByMangledNameInContext(n, l, c, a);
}

__attribute__((swiftcall, visibility("default")))
const void *swift_getTypeByMangledNameInContextInMetadataState2(size_t s, const char *n, size_t l, const void *c, const void *const *a) {
    return swift_getTypeByMangledNameInContextInMetadataState(s, n, l, c, a);
}

__attribute__((swiftcall, visibility("default")))
const void *swift_getOpaqueTypeConformance2(const void * const *a, const void *d, unsigned i) {
    return swift_getOpaqueTypeConformance(a, d, i);
}


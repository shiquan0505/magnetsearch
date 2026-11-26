import SwiftUI

public extension View {
    func onChange<V: Equatable>(of value: V, initial: Bool, _ action: @escaping (V, V) -> Void) -> some View {
        let base = self.onChange(of: value, perform: { newValue in action(newValue, newValue) })
        if initial {
            return AnyView(base.onAppear { action(value, value) })
        }
        return AnyView(base)
    }
}

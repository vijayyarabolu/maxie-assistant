// Tells the running Maxie app to open/close the panel or Settings (NotchController's remote channel).
import Foundation

let what = CommandLine.arguments.dropFirst().joined(separator: " ")
DistributedNotificationCenter.default().postNotificationName(.init("dev.maxie.notch.panel"), object: what,
                                                             userInfo: nil, deliverImmediately: true)

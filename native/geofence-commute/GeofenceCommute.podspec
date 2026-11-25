require 'json'

package = JSON.parse(File.read(File.join(__dir__, 'package.json')))

Pod::Spec.new do |s|
  s.name = 'GeofenceCommute'
  s.version = package['version']
  s.summary = package['description']
  s.license = package['license'] || 'UNLICENSED'
  s.homepage = (package['repository'] || {})['url'] || 'https://github.com/sangja8412-lang/overtime-tracker'
  s.author = package['author'] || 'KYEORE LEE'
  s.source = { :git => s.homepage, :tag => s.version.to_s }
  s.source_files = 'ios/Sources/**/*.{swift,h,m,c,cc,mm,cpp}'
  s.ios.deployment_target = '14.0'
  s.dependency 'Capacitor'
  s.swift_version = '5.1'
  s.frameworks = 'CoreLocation', 'UserNotifications'
end

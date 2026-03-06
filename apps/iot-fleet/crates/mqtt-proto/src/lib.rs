//! Minimal MQTT v3.1.1 packet building and parsing for embedded devices.
//!
//! All functions are `no_std` compatible and use fixed-size `heapless::Vec` buffers.
//! Extracted from the smart-switch firmware with bug fixes (PINGREQ, DISCONNECT)
//! and a packet type identifier.

#![no_std]

use heapless::Vec;

/// MQTT packet types identified from the first byte of a packet.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PacketType {
    ConnAck,
    Publish,
    SubAck,
    PingResp,
    Other(u8),
}

/// Build a minimal MQTT v3.1.1 CONNECT packet.
///
/// Sets clean session flag and 60-second keepalive.
pub fn build_connect(client_id: &[u8]) -> Vec<u8, 64> {
    let mut packet = Vec::new();
    let remaining_len = 10 + 2 + client_id.len();

    // Fixed header
    let _ = packet.push(0x10); // CONNECT
    let _ = packet.push(remaining_len as u8);

    // Variable header - Protocol Name
    let _ = packet.push(0x00);
    let _ = packet.push(0x04);
    let _ = packet.extend_from_slice(b"MQTT");

    // Protocol Level (4 = v3.1.1)
    let _ = packet.push(0x04);

    // Connect Flags: Clean Session
    let _ = packet.push(0x02);

    // Keep Alive: 60 seconds
    let _ = packet.push(0x00);
    let _ = packet.push(0x3C);

    // Payload - Client Identifier
    let _ = packet.push(0x00);
    let _ = packet.push(client_id.len() as u8);
    let _ = packet.extend_from_slice(client_id);

    packet
}

/// Build an MQTT v3.1.1 CONNECT packet with Last Will and Testament (LWT).
///
/// Sets clean session, will flag, will QoS 1, will retain, and 60-second keepalive.
/// The will message is published by the broker if the client disconnects unexpectedly.
pub fn build_connect_with_lwt(
    client_id: &[u8],
    will_topic: &str,
    will_message: &[u8],
) -> Vec<u8, 128> {
    let mut packet = Vec::new();
    let remaining_len =
        10 + 2 + client_id.len() + 2 + will_topic.len() + 2 + will_message.len();

    // Fixed header
    let _ = packet.push(0x10); // CONNECT
    let _ = packet.push(remaining_len as u8);

    // Variable header - Protocol Name
    let _ = packet.push(0x00);
    let _ = packet.push(0x04);
    let _ = packet.extend_from_slice(b"MQTT");

    // Protocol Level (4 = v3.1.1)
    let _ = packet.push(0x04);

    // Connect Flags: Clean Session | Will Flag | Will QoS 1 | Will Retain
    // 0x26 = 0b0010_0110
    let _ = packet.push(0x26);

    // Keep Alive: 60 seconds
    let _ = packet.push(0x00);
    let _ = packet.push(0x3C);

    // Payload - Client Identifier
    let _ = packet.push(0x00);
    let _ = packet.push(client_id.len() as u8);
    let _ = packet.extend_from_slice(client_id);

    // Payload - Will Topic
    let _ = packet.push(0x00);
    let _ = packet.push(will_topic.len() as u8);
    let _ = packet.extend_from_slice(will_topic.as_bytes());

    // Payload - Will Message
    let _ = packet.push(0x00);
    let _ = packet.push(will_message.len() as u8);
    let _ = packet.extend_from_slice(will_message);

    packet
}

/// Build an MQTT PUBLISH packet (QoS 0, no packet identifier).
///
/// Supports remaining lengths up to 16383 bytes (two-byte encoding).
pub fn build_publish(topic: &str, payload: &[u8]) -> Vec<u8, 640> {
    let mut packet = Vec::new();
    let remaining_len = 2 + topic.len() + payload.len();

    // Fixed header
    let _ = packet.push(0x30); // PUBLISH, QoS 0, no retain

    // Remaining length (variable-length encoding, up to 2 bytes)
    if remaining_len < 128 {
        let _ = packet.push(remaining_len as u8);
    } else {
        let _ = packet.push((remaining_len % 128) as u8 | 0x80);
        let _ = packet.push((remaining_len / 128) as u8);
    }

    // Variable header - Topic Name
    let _ = packet.push(0x00);
    let _ = packet.push(topic.len() as u8);
    let _ = packet.extend_from_slice(topic.as_bytes());

    // Payload
    let _ = packet.extend_from_slice(payload);

    packet
}

/// Build an MQTT SUBSCRIBE packet for a single topic filter at QoS 0.
pub fn build_subscribe(topic: &str, packet_id: u16) -> Vec<u8, 128> {
    let mut packet = Vec::new();
    let remaining_len = 2 + 2 + topic.len() + 1;

    // Fixed header
    let _ = packet.push(0x82); // SUBSCRIBE (with required bit 1 set)
    let _ = packet.push(remaining_len as u8);

    // Variable header - Packet Identifier
    let _ = packet.push((packet_id >> 8) as u8);
    let _ = packet.push(packet_id as u8);

    // Payload - Topic Filter
    let _ = packet.push(0x00);
    let _ = packet.push(topic.len() as u8);
    let _ = packet.extend_from_slice(topic.as_bytes());

    // Requested QoS: 0
    let _ = packet.push(0x00);

    packet
}

/// Parse an incoming MQTT PUBLISH packet, returning (topic, payload) as string slices.
///
/// Returns `None` if the packet is not a PUBLISH or is malformed.
/// Only handles remaining lengths encoded in a single byte (< 128).
pub fn parse_publish(data: &[u8]) -> Option<(&str, &str)> {
    if data.is_empty() {
        return None;
    }

    // Check packet type (upper nibble)
    let pkt_type = data[0] & 0xF0;
    if pkt_type != 0x30 {
        return None;
    }

    if data.len() < 4 {
        return None;
    }

    let remaining_len = data[1] as usize;
    if data.len() < 2 + remaining_len {
        return None;
    }

    // Topic length (MSB, LSB)
    let topic_len = ((data[2] as usize) << 8) | (data[3] as usize);
    if data.len() < 4 + topic_len {
        return None;
    }

    let topic = core::str::from_utf8(&data[4..4 + topic_len]).ok()?;

    // If QoS > 0, skip the 2-byte packet identifier
    let qos = (data[0] >> 1) & 0x03;
    let payload_start = if qos > 0 {
        4 + topic_len + 2
    } else {
        4 + topic_len
    };

    if payload_start > 2 + remaining_len {
        return None;
    }

    let payload = core::str::from_utf8(&data[payload_start..2 + remaining_len]).ok()?;

    Some((topic, payload))
}

/// Build an MQTT PINGREQ packet.
///
/// This is critical for maintaining the connection when keepalive is set.
/// The firmware sets a 60-second keepalive in CONNECT, so PINGREQ must be
/// sent before 1.5x that interval (90 seconds) to prevent broker disconnect.
pub fn build_pingreq() -> Vec<u8, 2> {
    let mut packet = Vec::new();
    let _ = packet.push(0xC0); // PINGREQ
    let _ = packet.push(0x00); // Remaining length: 0
    packet
}

/// Build an MQTT DISCONNECT packet for clean session shutdown.
///
/// Sending DISCONNECT before closing the TCP connection tells the broker
/// this is an intentional disconnect, so it will NOT publish the LWT message.
pub fn build_disconnect() -> Vec<u8, 2> {
    let mut packet = Vec::new();
    let _ = packet.push(0xE0); // DISCONNECT
    let _ = packet.push(0x00); // Remaining length: 0
    packet
}

/// Identify the MQTT packet type from the first byte of received data.
///
/// Returns `None` if the data slice is empty.
pub fn packet_type(data: &[u8]) -> Option<PacketType> {
    if data.is_empty() {
        return None;
    }

    let type_nibble = data[0] & 0xF0;
    Some(match type_nibble {
        0x20 => PacketType::ConnAck,
        0x30 => PacketType::Publish,
        0x90 => PacketType::SubAck,
        0xD0 => PacketType::PingResp,
        other => PacketType::Other(other),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connect_packet_structure() {
        let pkt = build_connect(b"test-device");
        assert_eq!(pkt[0], 0x10); // CONNECT type
        assert_eq!(pkt[4..8], *b"MQTT");
        assert_eq!(pkt[8], 0x04); // Protocol level v3.1.1
        assert_eq!(pkt[9], 0x02); // Clean session
    }

    #[test]
    fn connect_with_lwt_has_will_flags() {
        let pkt = build_connect_with_lwt(b"dev1", "home/avail", b"offline");
        assert_eq!(pkt[0], 0x10);
        assert_eq!(pkt[9], 0x26); // Clean session | Will flag | Will QoS 1 | Will retain
    }

    #[test]
    fn publish_small_payload() {
        let pkt = build_publish("home/switch/state", b"ON");
        assert_eq!(pkt[0], 0x30);
        // Remaining length should be 2 + topic_len + payload_len
        let expected_remaining = 2 + 17 + 2;
        assert_eq!(pkt[1], expected_remaining as u8);
    }

    #[test]
    fn subscribe_packet_structure() {
        let pkt = build_subscribe("home/switch/set", 1);
        assert_eq!(pkt[0], 0x82);
        // Packet ID = 1
        assert_eq!(pkt[2], 0x00);
        assert_eq!(pkt[3], 0x01);
    }

    #[test]
    fn parse_publish_roundtrip() {
        let pkt = build_publish("test/topic", b"hello");
        let (topic, payload) = parse_publish(&pkt).unwrap();
        assert_eq!(topic, "test/topic");
        assert_eq!(payload, "hello");
    }

    #[test]
    fn pingreq_is_correct() {
        let pkt = build_pingreq();
        assert_eq!(pkt.len(), 2);
        assert_eq!(pkt[0], 0xC0);
        assert_eq!(pkt[1], 0x00);
    }

    #[test]
    fn disconnect_is_correct() {
        let pkt = build_disconnect();
        assert_eq!(pkt.len(), 2);
        assert_eq!(pkt[0], 0xE0);
        assert_eq!(pkt[1], 0x00);
    }

    #[test]
    fn packet_type_identification() {
        assert_eq!(packet_type(&[0x20, 0x02]), Some(PacketType::ConnAck));
        assert_eq!(packet_type(&[0x30, 0x05]), Some(PacketType::Publish));
        assert_eq!(packet_type(&[0x90, 0x03]), Some(PacketType::SubAck));
        assert_eq!(packet_type(&[0xD0, 0x00]), Some(PacketType::PingResp));
        assert_eq!(packet_type(&[0x40, 0x02]), Some(PacketType::Other(0x40)));
        assert_eq!(packet_type(&[]), None);
    }
}

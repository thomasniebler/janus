import Foundation

struct User: Decodable {
    let id: Int
    let username: String
    let isAdmin: Bool

    private enum CodingKeys: String, CodingKey {
        case id
        case username
        case isAdmin = "is_admin"
    }
}

struct Email: Decodable, Identifiable {
    let id: Int
    let sender: String
    let subject: String
    let snippet: String
    let priority: String
    let receivedAt: String

    private enum CodingKeys: String, CodingKey {
        case id
        case sender
        case subject
        case snippet
        case priority
        case receivedAt = "received_at"
    }
}

struct PlannerEvent: Decodable, Identifiable {
    let id: Int
    let title: String
    let startTime: String
    let endTime: String
    let location: String?

    private enum CodingKeys: String, CodingKey {
        case id
        case title
        case startTime = "start_time"
        case endTime = "end_time"
        case location
    }
}

struct PlannerTask: Decodable, Identifiable {
    let id: Int
    let title: String
    let status: String
    let urgency: String

    private enum CodingKeys: String, CodingKey {
        case id
        case title
        case status
        case urgency
    }
}

struct DashboardData: Decodable {
    let importantEmails: [Email]
    let allEmails: [Email]
    let events: [PlannerEvent]
    let tasks: [PlannerTask]
    let now: String

    private enum CodingKeys: String, CodingKey {
        case importantEmails = "important_emails"
        case allEmails = "all_emails"
        case events
        case tasks
        case now
    }
}

struct LoginRequest: Encodable {
    let username: String
    let password: String
}

struct LoginResponse: Decodable {
    let accessToken: String

    private enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
    }
}

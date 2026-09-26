output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Where ECS tasks run. Egress through NAT, no inbound from the internet."
  value       = aws_subnet.private[*].id
}

output "data_subnet_ids" {
  description = "Where RDS and ElastiCache run. NO route to the internet in either direction."
  value       = aws_subnet.data[*].id
}

output "alb_security_group_id" {
  value = aws_security_group.alb.id
}

output "ecs_security_group_id" {
  value = aws_security_group.ecs.id
}

output "rds_security_group_id" {
  value = aws_security_group.rds.id
}

output "redis_security_group_id" {
  value = aws_security_group.redis.id
}

output "vpc_cidr_block" {
  value = aws_vpc.this.cidr_block
}

output "private_subnet_cidr_blocks" {
  description = "Where the application tier (ECS tasks, in-VPC Lambdas) and the interface endpoints live."
  value       = aws_subnet.private[*].cidr_block
}

output "data_subnet_cidr_blocks" {
  description = "Where RDS and ElastiCache live. The code sandbox's network ACL denies these explicitly."
  value       = aws_subnet.data[*].cidr_block
}

output "endpoints_security_group_id" {
  description = "The interface endpoints' security group. A host outside the application tier is admitted to it through `endpoint_client_security_group_ids`, never by a rule attached from outside."
  value       = aws_security_group.endpoints.id
}
